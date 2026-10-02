"""Versioned read-only export v1 (O9 Phase 3, X2-X5): jobs, tasks and llm-call events as JSON.

Contract: docs/export-contract-v1.md. No auth, no writes. Items are built field by field from the
allowlists below; every exported string passes a value rule. Pages are a snapshot: only events with
`ingest_seq <= as_of` are read, and membership and sums come from those events alone. A recost or
repair that changes snapshotted rows bumps `export_state.revision` and expires open cursors.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_session
from jev_scorer import EVALUATOR_PROJECT
from routes.events import JOB_REF_RE, PRODUCERS, RUNTIMES, WORK_TYPES
from routes.jobs import _tag
from routes.tasks import _completion

router = APIRouter(prefix="/api/export/v1", tags=["export"])

SCHEMA_VERSION = 1  # export contract version: changes only on a breaking change
MAX_SPAN = timedelta(days=92)
DEFAULT_LIMIT, MAX_LIMIT = 500, 1000
LEGACY_RUNTIME = "hermes-agent"

EXPORT_TAG_KEYS = ("runtime", "producer", "job_ref", "work_type", "job_attempt")
_TOKENS = ("prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_creation_tokens")
_COST = ("priced_count", "unpriced_count", "cost_usd", "estimated_cost_usd", "cost_complete")
EXPORT_FIELDS = {
    "events": (
        "event_id", "client_event_id", "project_name", "session_id", "task_ref", "runtime", "runtime_inferred",
        "producer", "job_ref", "job_ref_conflict", "work_type", "job_attempt", "occurred_at", "recorded_at",
        "provider", "model", "pricing_model", "status", "error_type", "http_status", *_TOKENS, "reasoning_tokens",
        "cost_status", "cost_usd", "estimated_cost_usd", "process_time_ms", "ttft_ms", "attempt", "retry_count",
        "tool_call_count", "role", "complexity", "complexity_method",
    ),
    "tasks": (
        "task_ref", "project_name", "session_id", "runtimes", "runtime_inferred", "job_ref", "work_type",
        "work_types", "hierarchy_status", "parent_task_ref", "root_task_ref", "first_event_at", "last_event_at",
        "wall_time_ms", "completion", "completed_at", "llm_request_count", *_TOKENS, *_COST, "conflict_count",
        "updated_at",
    ),
    "jobs": (
        "job_ref", "runtimes", "runtime_inferred", "work_type", "work_types", "attempts", "projects", "task_count",
        "llm_request_count", *_TOKENS, *_COST, "first_event_at", "last_event_at", "conflict_count",
        "conflict_task_count", "updated_at",
    ),
}

# Value rules (backend.md "Shared definitions"): a key allowlist is not enough.
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
ERROR_CLASS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
STATUSES = ("success", "error", "timeout", "cancelled")
COST_STATUSES = ("priced", "unpriced", "partial", "estimated", "legacy")
ROLES = ("primary", "subagent", "evaluator")
HIERARCHY = ("root", "child", "unknown")
COMPLEXITY_METHODS = ("request-shape-v1",)
_ESTIMATED = ("partial", "estimated", "legacy")


def _safe_id(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    value = str(value)
    return value if SAFE_ID_RE.fullmatch(value) else "p-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _model(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not MODEL_RE.fullmatch(value) or "//" in value:
        return None
    return value


def _error_class(value: Any) -> Optional[str]:
    if value is None:
        return None
    last = str(value).rsplit(".", 1)[-1]
    return last if ERROR_CLASS_RE.fullmatch(last) else "other"


def _closed(value: Any, allowed: tuple, fallback: Optional[str]) -> Optional[str]:
    return value if value in allowed else fallback


def _job(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and JOB_REF_RE.fullmatch(value) else None


def _hex(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and HEX32_RE.fullmatch(value) else None


def _split(value: Any) -> list[str]:
    return sorted({part for part in str(value).split(",") if part}) if value else []


# --- SQL ------------------------------------------------------------------------------------------------------

_TASK_JOIN = ("tasks t ON t.project_name = e.project_name AND t.session_id = COALESCE(e.session_id, '') "
              "AND t.turn_id = e.turn_id")
# Snapshot job of a task: the job_ref tag of its lowest-sequence snapshot event that carries one
# (SQLite returns the bare column of the MIN() row). Not tasks.job_ref, which later events can set.
_TJ = f"""tj AS (
    SELECT t.id AS task_ref, json_extract(e.tags_json, '$.job_ref') AS job, MIN(e.ingest_seq) AS first_seq
    FROM token_events e JOIN {_TASK_JOIN}
    WHERE e.ingest_seq <= :as_of AND e.event_type <> 'session' AND json_valid(e.tags_json)
      AND json_extract(e.tags_json, '$.job_ref') IS NOT NULL
    GROUP BY t.id
)"""
# Counted snapshot llm events: never evaluator (JEV) usage, never an invalid runtime/producer pair.
_EV = f"""ev AS (
    SELECT e.id, e.client_event_id, e.project_name, e.session_id, e.occurred_at, e.recorded_at, e.provider,
           e.model, e.pricing_model, e.status, e.error_type, e.http_status, e.prompt_tokens, e.completion_tokens,
           e.cache_read_tokens, e.cache_creation_tokens, e.reasoning_tokens, e.cost_status, e.estimated_cost_usd,
           e.process_time_ms, e.ttft_ms, e.attempt, e.retry_count, e.tool_call_count, e.role, e.complexity,
           e.complexity_method, e.ingest_seq,
           COALESCE(e.occurred_at, e.recorded_at) AS ts,
           t.id AS task_ref,
           {_tag('runtime')} AS runtime, {_tag('producer')} AS producer, {_tag('work_type')} AS work_type,
           {_tag('job_attempt')} AS job_attempt, {_tag('job_ref')} AS own_job,
           CASE WHEN t.id IS NOT NULL THEN tj.job ELSE {_tag('job_ref')} END AS job
    FROM token_events e
    LEFT JOIN {_TASK_JOIN}
    LEFT JOIN tj ON tj.task_ref = t.id
    WHERE e.ingest_seq <= :as_of AND e.event_type = 'llm_request'
      AND NOT (e.project_name = :evaluator_project AND COALESCE(e.role, '') = 'evaluator')
      AND COALESCE({_tag('attribution_invalid')}, 0) = 0
)"""
_CONFLICT = "(ev.own_job IS NOT NULL AND ev.own_job <> ev.job)"
_SUMS = f"""
    MIN(ev.ts) AS first_event_at, MAX(ev.ts) AS last_event_at, COUNT(*) AS llm_request_count,
    {", ".join(f"COALESCE(SUM(ev.{n}), 0) AS {n}" for n in _TOKENS)},
    SUM(ev.cost_status = 'priced') AS priced_count,
    SUM(CASE WHEN ev.cost_status = 'priced' THEN COALESCE(ev.estimated_cost_usd, 0) ELSE 0 END) AS priced_sum,
    SUM(ev.cost_status = 'unpriced') AS unpriced_count,
    SUM(ev.cost_status IN {_ESTIMATED}) AS estimated_count,
    SUM(CASE WHEN ev.cost_status IN {_ESTIMATED} THEN COALESCE(ev.estimated_cost_usd, 0) ELSE 0 END) AS estimated_sum,
    group_concat(DISTINCT COALESCE(ev.runtime, '{LEGACY_RUNTIME}')) AS runtimes,
    MAX(ev.runtime IS NULL) AS runtime_inferred,
    group_concat(DISTINCT ev.work_type) AS work_types,
    SUM({_CONFLICT}) AS conflict_count"""
_PERIOD = "{ts} >= :start AND {ts} < :end"

_SQL = {
    "events": f"""WITH {_TJ}, {_EV}
SELECT ev.* FROM ev
WHERE {_PERIOD.format(ts='ev.ts')} AND ev.ingest_seq > :after
ORDER BY ev.ingest_seq LIMIT :n""",
    # Start-time cohort: tasks whose first snapshot event is in the period; sums over all their snapshot events.
    "tasks": f"""WITH {_TJ}, {_EV}, ta AS (
    SELECT ev.task_ref, {_SUMS}
    FROM ev WHERE ev.task_ref IS NOT NULL GROUP BY ev.task_ref
)
SELECT ta.*, t.project_name, t.session_id, t.hierarchy_status, t.parent_task_ref, t.root_task_ref,
       t.completion, t.completed_at, t.last_seen_at, t.updated_at, tj.job AS job
FROM ta JOIN tasks t ON t.id = ta.task_ref LEFT JOIN tj ON tj.task_ref = ta.task_ref
WHERE {_PERIOD.format(ts='ta.first_event_at')} AND ta.task_ref > :after
ORDER BY ta.task_ref LIMIT :n""",
    # Start-time cohort of jobs; an event's job is its task's snapshot job, else its own tag.
    "jobs": f"""WITH {_TJ}, {_EV}, ja AS (
    SELECT ev.job AS job_ref, {_SUMS},
           COUNT(DISTINCT ev.task_ref) AS task_count,
           COUNT(DISTINCT CASE WHEN {_CONFLICT} THEN ev.task_ref END) AS conflict_task_count,
           group_concat(DISTINCT ev.job_attempt) AS attempts,
           group_concat(DISTINCT ev.project_name) AS projects,
           MAX(ev.recorded_at) AS updated_at
    FROM ev WHERE ev.job IS NOT NULL GROUP BY ev.job
)
SELECT * FROM ja WHERE {_PERIOD.format(ts='ja.first_event_at')} AND ja.job_ref > :after
ORDER BY ja.job_ref LIMIT :n""",
}
# Staleness: latest recorded_at per runtime over all snapshot events (all time, any event type).
_STALENESS_SQL = f"""
SELECT COALESCE({_tag('runtime')}, '{LEGACY_RUNTIME}') AS runtime, MAX(e.recorded_at) AS latest
FROM token_events e
WHERE e.ingest_seq <= :as_of
  AND NOT (e.project_name = :evaluator_project AND COALESCE(e.role, '') = 'evaluator')
  AND COALESCE({_tag('attribution_invalid')}, 0) = 0
GROUP BY 1"""
_COVERAGE_SQL = f"""WITH {_TJ}, {_EV}
SELECT DISTINCT COALESCE(ev.runtime, '{LEGACY_RUNTIME}') FROM ev WHERE {_PERIOD.format(ts='ev.ts')}"""


# --- items ----------------------------------------------------------------------------------------------------


def _event_item(row) -> dict[str, Any]:
    tagged = row["runtime"] is not None
    producer = _closed(row["producer"], PRODUCERS, "other") if tagged else None
    cc = producer == "claude-code-hook"
    job = _job(row["job"])
    own = _job(row["own_job"])
    status = row["cost_status"]
    item: dict[str, Any] = {
        "event_id": _safe_id(row["id"]),
        "client_event_id": _safe_id(row["client_event_id"]),
        "project_name": row["project_name"],
        "session_id": _safe_id(row["session_id"]),
    }
    if row["task_ref"] is not None:
        item["task_ref"] = _hex(row["task_ref"])
    item["runtime"] = _closed(row["runtime"], RUNTIMES, "other") if tagged else LEGACY_RUNTIME
    item["runtime_inferred"] = not tagged
    if producer is not None:
        item["producer"] = producer
    if job is not None:
        item["job_ref"] = job
    item["job_ref_conflict"] = own is not None and own != job
    if row["work_type"] is not None:
        item["work_type"] = _closed(row["work_type"], WORK_TYPES, "other")
    if isinstance(row["job_attempt"], int) and row["job_attempt"] >= 1:
        item["job_attempt"] = row["job_attempt"]
    item.update({
        "occurred_at": row["occurred_at"],
        "recorded_at": row["recorded_at"],
        "provider": _safe_id(row["provider"]),
        "model": _model(row["model"]),
        "pricing_model": _model(row["pricing_model"]),
        "status": _closed(row["status"], STATUSES, "other"),
        "error_type": _error_class(row["error_type"]),
        "http_status": row["http_status"],
        **{name: int(row[name] or 0) for name in _TOKENS},
    })
    if not cc:  # not reported by Claude Code transcripts: the stored 0 is not a measurement
        item["reasoning_tokens"] = int(row["reasoning_tokens"] or 0)
    item.update({
        "cost_status": _closed(status, COST_STATUSES, "other"),
        "cost_usd": float(row["estimated_cost_usd"]) if status == "priced" and row["estimated_cost_usd"] is not None
        else None,
        "estimated_cost_usd": float(row["estimated_cost_usd"])
        if status in _ESTIMATED and row["estimated_cost_usd"] is not None else None,
        "process_time_ms": row["process_time_ms"],
    })
    if not cc:
        item["ttft_ms"] = row["ttft_ms"]
    item.update({
        "attempt": row["attempt"],
        "retry_count": row["retry_count"],
        "tool_call_count": row["tool_call_count"],
        "role": _closed(row["role"], ROLES, "other") if row["role"] is not None else None,
        "complexity": row["complexity"],
    })
    if row["complexity_method"] in COMPLEXITY_METHODS:
        item["complexity_method"] = row["complexity_method"]
    return item


def _aggregate(row) -> dict[str, Any]:
    """Fields shared by tasks and jobs (same cost semantics as GET /api/jobs)."""
    priced = int(row["priced_count"] or 0)
    unpriced = int(row["unpriced_count"] or 0)
    count = int(row["llm_request_count"] or 0)
    return {
        "llm_request_count": count,
        **{name: int(row[name] or 0) for name in _TOKENS},
        "priced_count": priced,
        "unpriced_count": unpriced,
        "cost_usd": float(row["priced_sum"] or 0.0) if priced else None,  # an unpriced task/job is never zero
        "estimated_cost_usd": float(row["estimated_sum"] or 0.0),
        "cost_complete": unpriced == 0 and count > 0 and int(row["estimated_count"] or 0) == 0,
    }


def _work_types(item: dict, row) -> None:
    work_types = [_closed(w, WORK_TYPES, "other") for w in _split(row["work_types"])]
    work_types = sorted(set(work_types))
    if work_types:
        item["work_type"] = work_types[0] if len(work_types) == 1 else None
    item["work_types"] = work_types


def _runtimes(row) -> list[str]:
    return sorted({_closed(r, RUNTIMES, "other") for r in _split(row["runtimes"])})


def _task_item(row, now: datetime) -> dict[str, Any]:
    first, last = _parse(row["first_event_at"]), _parse(row["last_event_at"])
    completion, completed_at = _completion(row, now)
    item: dict[str, Any] = {
        "task_ref": _hex(row["task_ref"]),
        "project_name": row["project_name"],
        "session_id": _safe_id(row["session_id"]),
        "runtimes": _runtimes(row),
        "runtime_inferred": bool(row["runtime_inferred"]),
    }
    job = _job(row["job"])
    if job is not None:
        item["job_ref"] = job
    _work_types(item, row)
    item.update({
        "hierarchy_status": _closed(row["hierarchy_status"], HIERARCHY, "unknown"),
        "parent_task_ref": _hex(row["parent_task_ref"]),
        "root_task_ref": _hex(row["root_task_ref"]),
        "first_event_at": row["first_event_at"],
        "last_event_at": row["last_event_at"],
        "wall_time_ms": int((last - first).total_seconds() * 1000) if first and last else 0,
        "completion": completion,
        "completed_at": completed_at,
        **_aggregate(row),
        "conflict_count": int(row["conflict_count"] or 0),
        "updated_at": row["updated_at"],
    })
    return item


def _job_item(row) -> dict[str, Any]:
    item: dict[str, Any] = {
        "job_ref": row["job_ref"],
        "runtimes": _runtimes(row),
        "runtime_inferred": bool(row["runtime_inferred"]),
    }
    _work_types(item, row)
    item.update({
        "attempts": sorted({int(a) for a in _split(row["attempts"]) if a.isdigit() and int(a) >= 1}),
        "projects": _split(row["projects"]),
        "task_count": int(row["task_count"] or 0),
        **_aggregate(row),
        "first_event_at": row["first_event_at"],
        "last_event_at": row["last_event_at"],
        "conflict_count": int(row["conflict_count"] or 0),
        "conflict_task_count": int(row["conflict_task_count"] or 0),
        "updated_at": row["updated_at"],
    })
    return item


# --- request handling -----------------------------------------------------------------------------------------


def _error(status: int, code: str, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "schema_version": SCHEMA_VERSION},
                        headers=headers)


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: Optional[str]) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


_INT64_MAX = 2**63 - 1


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str, dataset: str, start: str, end: str) -> Optional[dict]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("d") != dataset:
        return None
    if payload.get("f") != start or payload.get("t") != end:  # a cursor is bound to its dataset and range
        return None
    key_type = int if dataset == "events" else str
    if not all(isinstance(payload.get(k), int) and not isinstance(payload.get(k), bool)
               and 0 <= payload[k] <= _INT64_MAX for k in ("s", "r")):
        return None
    if not isinstance(payload.get("e"), str) or type(payload.get("k")) is not key_type:
        return None
    if key_type is int and not 0 <= payload["k"] <= _INT64_MAX:  # SQLite binds signed 64-bit only
        return None
    return payload


async def _state(session: AsyncSession) -> tuple[int, int, str]:
    row = (await session.execute(text("SELECT last_seq, revision, epoch FROM export_state WHERE id = 1"))).one()
    return int(row[0]), int(row[1]), str(row[2])


async def _export(dataset: str, start_raw: Optional[str], end_raw: Optional[str], limit_raw: Optional[str],
                  cursor_raw: Optional[str], session: AsyncSession) -> JSONResponse | dict:
    start, end = _parse(start_raw), _parse(end_raw)
    if start is None or end is None or start >= end or end - start > MAX_SPAN:
        return _error(400, "invalid_range")
    if limit_raw is None:
        limit = DEFAULT_LIMIT
    elif limit_raw.isascii() and limit_raw.isdigit() and len(limit_raw) <= 6 and 1 <= int(limit_raw) <= MAX_LIMIT:
        limit = int(limit_raw)
    else:
        return _error(400, "invalid_limit")
    start_s, end_s = _fmt(start), _fmt(end)
    cursor = None
    if cursor_raw is not None:
        cursor = _decode_cursor(cursor_raw, dataset, start_s, end_s)
        if cursor is None:
            return _error(400, "invalid_cursor")

    try:
        if cursor is None:
            as_of, revision, epoch = await _state(session)
            after: Any = 0 if dataset == "events" else ""
        else:
            as_of, revision, epoch, after = cursor["s"], cursor["r"], cursor["e"], cursor["k"]
        params = {"as_of": as_of, "evaluator_project": EVALUATOR_PROJECT, "start": start_s, "end": end_s}
        rows = (await session.execute(text(_SQL[dataset]), {**params, "after": after, "n": limit + 1})).mappings().all()
        stale = dict((await session.execute(text(_STALENESS_SQL), params)).all())
        measured = {r[0] for r in (await session.execute(text(_COVERAGE_SQL), params)).all()}
        if cursor is not None and (await _state(session))[1:] != (revision, epoch):
            # read after the page: a recost/repair that landed before or during it is always seen
            return _error(409, "snapshot_expired")
    except OperationalError:
        return _error(503, "busy", headers={"Retry-After": "5"})

    complete = len(rows) <= limit
    rows = rows[:limit]
    now = datetime.now(timezone.utc)
    if dataset == "events":
        items = [_event_item(r) for r in rows]
    elif dataset == "tasks":
        items = [_task_item(r, now) for r in rows]
    else:
        items = [_job_item(r) for r in rows if _job(r["job_ref"]) is not None]
    next_cursor = None
    if not complete:
        last = rows[-1]
        key = last["ingest_seq"] if dataset == "events" else last["task_ref" if dataset == "tasks" else "job_ref"]
        next_cursor = _encode_cursor({"v": 1, "d": dataset, "f": start_s, "t": end_s, "s": as_of, "r": revision,
                                      "e": epoch, "k": key})
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset": dataset,
        "generated_at": _fmt(now),
        "period": {"from": start_s, "to": end_s},
        "fields": list(EXPORT_FIELDS[dataset]),
        "staleness": {runtime: stale.get(runtime) for runtime in RUNTIMES},
        "coverage": {"measured": [r for r in RUNTIMES if r in measured],
                     "not_measured": [r for r in RUNTIMES if r not in measured]},
        "items": items,
        "next_cursor": next_cursor,
        "complete": complete,
    }


def _route(dataset: str):
    async def handler(
        start: Optional[str] = Query(default=None, alias="from"),
        end: Optional[str] = Query(default=None, alias="to"),
        limit: Optional[str] = None,
        cursor: Optional[str] = None,
        session: AsyncSession = Depends(get_session),
    ):
        return await _export(dataset, start, end, limit, cursor, session)

    handler.__name__ = f"export_{dataset}"
    return handler


for _dataset in EXPORT_FIELDS:
    router.add_api_route(f"/{_dataset}", _route(_dataset), methods=["GET"])
