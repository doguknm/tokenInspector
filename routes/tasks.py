"""Tasks API (backend.md Endpoints; task = one Hermes turn per the D0 Drift Log).

Static paths are registered before `/api/tasks/{task_ref}`. Prompt text is only ever returned
by the detail endpoint with an authenticated include_prompt=true, and only while retained.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

import database
import features
import jev_scorer
import retention
from auth import require_sensitive_auth
from database import get_session
from redaction import scrub_text

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
RUBRIC_VERSION = "difficulty-v0"
INFERRED_AFTER = timedelta(minutes=60)
MAX_CHILDREN = 200
_LABELER_RE = re.compile(r"^[a-z0-9._-]{1,32}$")
_TASK_REF_RE = re.compile(r"^[0-9a-f]{32}$")


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def prompt_state(row: dict, now: str) -> str:
    if row.get("prompt_purged_at"):
        return "purged"
    if row.get("prompt_text") is not None:
        expires = row.get("prompt_expires_at")
        return "expired" if expires is None or expires <= now else "retained"
    return "none"


def _completion(row: dict, now_dt: datetime) -> tuple[str, Optional[str]]:
    if row["completion"]:
        return row["completion"], row["completed_at"]
    last_seen = _parse(row["last_seen_at"])
    if last_seen is not None and last_seen < now_dt - INFERRED_AFTER:
        return "inferred", row["last_seen_at"]
    return "open", None


async def _aggregates(session: AsyncSession, rows: list[dict]) -> dict[str, dict]:
    if not rows:
        return {}
    keys = {(r["project_name"], r["session_id"], r["turn_id"]): r["id"] for r in rows}
    params: dict[str, Any] = {}
    placeholders = []
    for i, (project, session_id, turn) in enumerate(keys):
        params.update({f"p{i}": project, f"s{i}": session_id, f"t{i}": turn})
        placeholders.append(f"(:p{i}, :s{i}, :t{i})")
    query = text(
        "SELECT project_name, COALESCE(session_id, '') AS s, turn_id, "
        "SUM(event_type = 'llm_request') AS llm_request_count, "
        "SUM(event_type = 'tool_call') AS tool_call_count, "
        "SUM(event_type = 'llm_request' AND status <> 'success') AS error_count, "
        "COALESCE(SUM(CASE WHEN event_type = 'llm_request' THEN retry_count END), 0) AS retry_count, "
        "COALESCE(SUM(CASE WHEN event_type = 'llm_request' THEN prompt_tokens END), 0) AS prompt_tokens, "
        "COALESCE(SUM(CASE WHEN event_type = 'llm_request' THEN completion_tokens END), 0) AS completion_tokens, "
        "COALESCE(SUM(CASE WHEN event_type = 'llm_request' THEN cache_read_tokens END), 0) AS cache_read_tokens, "
        "COALESCE(SUM(CASE WHEN event_type = 'llm_request' THEN cache_creation_tokens END), 0) "
        "AS cache_creation_tokens, "
        "SUM(event_type = 'llm_request' AND cost_status = 'priced') AS priced_count, "
        "COALESCE(SUM(CASE WHEN event_type = 'llm_request' AND cost_status = 'priced' "
        "THEN estimated_cost_usd END), 0) AS priced_cost, "
        "COALESCE(SUM(CASE WHEN event_type = 'llm_request' AND cost_status IN ('partial', 'estimated', 'legacy') "
        "THEN estimated_cost_usd END), 0) AS estimated_cost, "
        "SUM(event_type = 'llm_request' AND cost_status = 'unpriced') AS unpriced_count "
        "FROM token_events WHERE event_type <> 'session' "
        f"AND (project_name, COALESCE(session_id, ''), turn_id) IN (VALUES {', '.join(placeholders)}) "
        "GROUP BY project_name, s, turn_id"
    )
    result: dict[str, dict] = {}
    for row in (await session.execute(query, params)).mappings():
        ref = keys[(row["project_name"], row["s"], row["turn_id"])]
        result[ref] = dict(row)
    return result


async def _latest_jev(session: AsyncSession, refs: list[str]) -> dict[str, dict]:
    if not refs:
        return {}
    params = {f"r{i}": ref for i, ref in enumerate(refs)}
    rows = (
        await session.execute(
            text(
                "SELECT task_ref, raw_score, confidence, probabilities_json, legend_json, provider_used, "
                "rubric_version, evaluated_at FROM task_evaluations WHERE evaluator = 'jev' AND status = 'ok' "
                f"AND rubric_version = :rubric AND task_ref IN ({', '.join(':' + k for k in params)}) "
                "ORDER BY evaluated_at DESC, id DESC"
            ),
            {**params, "rubric": RUBRIC_VERSION},
        )
    ).mappings()
    latest: dict[str, dict] = {}
    for row in rows:
        if row["task_ref"] in latest:
            continue
        latest[row["task_ref"]] = {
            "raw_score": row["raw_score"],
            "display_score": None if row["raw_score"] is None else row["raw_score"] + 1,
            "confidence": row["confidence"],
            "probabilities": json.loads(row["probabilities_json"]) if row["probabilities_json"] else None,
            "legend": json.loads(row["legend_json"]) if row["legend_json"] else None,
            "provider_used": row["provider_used"],
            "rubric_version": row["rubric_version"],
            "evaluated_at": row["evaluated_at"],
        }
    return latest


async def _counts(session: AsyncSession, refs: list[str], sql: str) -> dict[str, int]:
    if not refs:
        return {}
    params = {f"r{i}": ref for i, ref in enumerate(refs)}
    query = text(sql.format(refs=", ".join(":" + k for k in params)))
    return {row[0]: row[1] for row in (await session.execute(query, params)).all()}


async def build_items(session: AsyncSession, rows: list[dict]) -> list[dict]:
    now_dt = datetime.now(timezone.utc)
    now = _fmt(now_dt)
    refs = [r["id"] for r in rows]
    aggregates = await _aggregates(session, rows)
    jev = await _latest_jev(session, refs)
    children = await _counts(
        session, refs, "SELECT parent_task_ref, COUNT(*) FROM tasks WHERE parent_task_ref IN ({refs}) GROUP BY 1"
    )
    labels = await _counts(
        session,
        refs,
        "SELECT task_ref, COUNT(*) FROM task_evaluations WHERE evaluator = 'human' AND status = 'ok' "
        "AND task_ref IN ({refs}) GROUP BY 1",
    )
    items = []
    for row in rows:
        agg = aggregates.get(row["id"], {})
        completion, completed_at = _completion(row, now_dt)
        state = prompt_state(row, now)
        first, last = _parse(row["first_seen_at"]), _parse(row["last_seen_at"])
        tokens = {k: int(agg.get(k) or 0) for k in
                  ("prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_creation_tokens")}
        items.append(
            {
                "task_ref": row["id"],
                "project_name": row["project_name"],
                "session_id": row["session_id"] or None,
                "turn_id": row["turn_id"],
                "source_task_id": row["source_task_id"],
                "parent_task_ref": row["parent_task_ref"],
                "root_task_ref": row["root_task_ref"],
                "hierarchy_status": row["hierarchy_status"],
                "child_count": int(children.get(row["id"], 0)),
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "wall_time_ms": int((last - first).total_seconds() * 1000) if first and last else 0,
                "completion": completion,
                "completed_at": completed_at,
                "llm_request_count": int(agg.get("llm_request_count") or 0),
                "tool_call_count": int(agg.get("tool_call_count") or 0),
                "error_count": int(agg.get("error_count") or 0),
                "retry_count": int(agg.get("retry_count") or 0),
                **tokens,
                "total_tokens": sum(tokens.values()),
                "cost_usd": float(agg["priced_cost"]) if agg.get("priced_count") else None,
                "estimated_cost_usd": float(agg.get("estimated_cost") or 0.0),
                "unpriced_count": int(agg.get("unpriced_count") or 0),
                "start_complexity": row["start_complexity"],
                "start_complexity_method": row["start_complexity_method"],
                "prompt_state": state,
                "has_prompt": state == "retained",
                "prompt_purged": bool(row["prompt_purged_at"]),
                "prompt_expires_at": row["prompt_expires_at"],
                "jev": jev.get(row["id"]),
                "human_label_count": int(labels.get(row["id"], 0)),
            }
        )
    return items


@router.get("")
async def list_tasks(
    days: int = 30,
    project: Optional[str] = None,
    evaluated: Optional[bool] = None,
    has_prompt: Optional[bool] = None,
    root_only: bool = False,
    allowed_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    x_ingest_token: Optional[str] = Header(default=None, alias="X-Ingest-Token"),
    origin: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    if allowed_only:  # allowlist membership is never probeable without the token
        await require_sensitive_auth(x_ingest_token=x_ingest_token, origin=origin)
    days = min(max(days, 1), 3650)
    now = _fmt(datetime.now(timezone.utc))
    cutoff = _fmt(datetime.now(timezone.utc) - timedelta(days=days))
    where = ["t.last_seen_at >= :cutoff"]
    params: dict[str, Any] = {"cutoff": cutoff, "now": now}
    if project:
        where.append("t.project_name = :project")
        params["project"] = project
    jev_ok = ("EXISTS (SELECT 1 FROM task_evaluations e WHERE e.task_ref = t.id AND e.evaluator = 'jev' "
              "AND e.status = 'ok')")
    if evaluated is not None:
        where.append(jev_ok if evaluated else f"NOT {jev_ok}")
    retained = "(t.prompt_text IS NOT NULL AND t.prompt_purged_at IS NULL AND t.prompt_expires_at > :now)"
    if has_prompt is not None:
        where.append(retained if has_prompt else f"NOT {retained}")
    if root_only:
        where.append("t.hierarchy_status = 'root'")
    if allowed_only:
        allowed = sorted(features.current().allowed_projects)
        names = {f"ap{i}": name for i, name in enumerate(allowed)}
        where.append(f"t.project_name IN ({', '.join(':' + k for k in names)})" if names else "0")
        params.update(names)
    clause = " AND ".join(where)
    total = (await session.execute(text(f"SELECT COUNT(*) FROM tasks t WHERE {clause}"), params)).scalar_one()
    rows = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    f"SELECT t.* FROM tasks t WHERE {clause} ORDER BY t.last_seen_at DESC, t.id ASC "
                    "LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        ).mappings()
    ]
    projects = [
        r[0]
        for r in (
            await session.execute(
                text("SELECT DISTINCT project_name FROM tasks WHERE last_seen_at >= :cutoff ORDER BY 1"),
                {"cutoff": cutoff},
            )
        ).all()
    ]
    return {
        "items": await build_items(session, rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "projects": projects,
    }


class EvaluateIn(BaseModel):
    task_refs: list[str] = Field(min_length=1, max_length=100)


async def _skip_reason(session: AsyncSession, ref: str, now: str) -> Optional[str]:
    if not _TASK_REF_RE.fullmatch(ref):
        return "not_found"
    row = (await session.execute(text("SELECT * FROM tasks WHERE id = :id"), {"id": ref})).mappings().first()
    if row is None:
        return "not_found"
    if row["project_name"] == jev_scorer.EVALUATOR_PROJECT:
        return "evaluator_task"
    reason = await jev_scorer.project_gate(session, ref)
    if reason:
        return reason
    state = prompt_state(dict(row), now)
    if state != "retained":
        return {"none": "no_prompt", "expired": "prompt_expired", "purged": "prompt_purged"}[state]
    done = (
        await session.execute(
            text(
                "SELECT 1 FROM task_evaluations WHERE task_ref = :t AND evaluator = 'jev' AND status = 'ok' "
                "AND rubric_version = :r AND input_hash = :h"
            ),
            {"t": ref, "r": RUBRIC_VERSION, "h": jev_scorer.input_hash(row["prompt_text"])},
        )
    ).first()
    return "already_scored" if done else None


@router.post("/evaluate", dependencies=[Depends(require_sensitive_auth)])
async def evaluate(
    body: EvaluateIn,
    response: Response,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    state = features.current()
    refs = list(dict.fromkeys(body.task_refs))
    if not state.jev_enabled:
        response.status_code = 200
        return {
            "run_id": None, "enabled": False, "queued": 0,
            "skipped": [{"task_ref": r, "reason": "jev_disabled"} for r in refs], "retry_not_before": None,
        }
    now_dt = jev_scorer.now()  # same clock as the worker's cooldown checks
    now = _fmt(now_dt)
    skipped, queued = [], []
    for ref in refs:
        reason = await _skip_reason(session, ref, now)
        if reason:
            skipped.append({"task_ref": ref, "reason": reason})
        else:
            queued.append(ref)
    blocked = jev_scorer.blocked_until(await jev_scorer.cooldowns(session), now_dt, state.budget.max_retry_wait_s)
    if blocked is not None or not queued:
        response.status_code = 200
        return {
            "run_id": None, "enabled": True, "queued": 0, "skipped": skipped,
            "retry_not_before": _fmt(blocked) if blocked else None,
        }
    run_id = str(uuid.uuid4())
    try:
        await session.execute(
            text(
                "INSERT INTO evaluator_runs (id, evaluator, status, requested_count, queued_count, queued_at) "
                "VALUES (:id, 'jev', 'queued', :req, :q, :at)"
            ),
            {"id": run_id, "req": len(refs), "q": len(queued), "at": now},
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="evaluation_in_progress")
    background_tasks.add_task(jev_scorer.run_evaluation, run_id, queued)
    response.status_code = 202
    return {"run_id": run_id, "enabled": True, "queued": len(queued), "skipped": skipped, "retry_not_before": None}


async def _run_summary(session: AsyncSession, run_id: str) -> Optional[dict]:
    run = (
        await session.execute(text("SELECT * FROM evaluator_runs WHERE id = :id"), {"id": run_id})
    ).mappings().first()
    if run is None:
        return None
    counts = dict(
        (
            await session.execute(
                text("SELECT status, COUNT(*) FROM task_evaluations WHERE run_id = :id GROUP BY status"),
                {"id": run_id},
            )
        ).all()
    )
    return {
        "run_id": run["id"], "status": run["status"], "requested_count": run["requested_count"],
        "queued_count": run["queued_count"],
        "attempted": sum(v for k, v in counts.items() if k != "skipped"),
        **{k: int(counts.get(k, 0)) for k in ("ok", "error", "rate_limited", "deferred", "skipped")},
        "stop_reason": run["stop_reason"], "retry_not_before": run["retry_not_before"],
        "queued_at": run["queued_at"], "started_at": run["started_at"], "finished_at": run["finished_at"],
    }


@router.get("/evaluator-runs/{run_id}")
async def evaluator_run(run_id: str, session: AsyncSession = Depends(get_session)):
    summary = await _run_summary(session, run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return summary


@router.get("/evaluator-status")
async def evaluator_status(session: AsyncSession = Depends(get_session)):
    state = features.current()
    budget = state.budget or features.Budget(0.0, 0.0, 0, 0.0)
    totals = await jev_scorer.spend(session, jev_scorer.now())
    current = (
        await session.execute(
            text("SELECT id FROM evaluator_runs WHERE status IN ('queued', 'running') ORDER BY queued_at DESC LIMIT 1")
        )
    ).first()
    last = (
        await session.execute(
            text(
                "SELECT id FROM evaluator_runs WHERE status NOT IN ('queued', 'running') "
                "ORDER BY finished_at DESC, queued_at DESC LIMIT 1"
            )
        )
    ).first()
    current_summary = await _run_summary(session, current[0]) if current else None
    last_summary = await _run_summary(session, last[0]) if last else None
    cool = await jev_scorer.cooldowns(session)
    return {
        "enabled": state.jev_enabled,
        "disabled_reason": state.jev_disabled_reason,
        "credentials_present": state.credentials_present,
        "running": current is not None,
        "current_run": None if not current_summary else {
            k: current_summary[k] for k in ("run_id", "status", "queued_count", "attempted")
        },
        "last_run": None if not last_summary else {
            k: last_summary[k] for k in ("run_id", "status", "stop_reason", "retry_not_before", "finished_at")
        },
        "spent_today_usd": totals["day_usd"],
        "spent_month_usd": totals["month_usd"],
        "calls_today": totals["calls_day"],
        "budget_day_usd": budget.day_usd,
        "budget_month_usd": budget.month_usd,
        "max_calls_per_day": budget.max_calls_per_day,
        "last_error_type": last_summary["stop_reason"] if last_summary else None,
        "provider_cooldowns": [
            {"provider": p, "not_before": jev_scorer._fmt(nb)} for p, nb in sorted(cool.items())
        ],
    }


@router.get("/retention-status")
async def retention_status(session: AsyncSession = Depends(get_session)):
    return await retention.status(session, database.DB_PATH)


@router.post("/purge-expired", dependencies=[Depends(require_sensitive_auth)])
async def purge_expired(dry_run: bool = True, session: AsyncSession = Depends(get_session)):
    return await retention.purge_expired(session, database.DB_PATH, dry_run=dry_run)


class LabelIn(BaseModel):
    label: Optional[int] = Field(default=None, ge=0, le=4)
    skipped: bool = False
    labeler: str
    rubric_version: Literal["difficulty-v0"] = RUBRIC_VERSION
    note: Optional[str] = Field(default=None, max_length=280)

    @field_validator("labeler")
    @classmethod
    def validate_labeler(cls, value: str) -> str:
        if not _LABELER_RE.fullmatch(value):
            raise ValueError("labeler must match ^[a-z0-9._-]{1,32}$")
        return value

    @model_validator(mode="after")
    def exactly_one(self) -> "LabelIn":
        if (self.label is None) == (not self.skipped):
            raise ValueError("give exactly one of label (0..4) or skipped=true")
        return self


async def _task_row(session: AsyncSession, task_ref: str) -> dict:
    if not _TASK_REF_RE.fullmatch(task_ref):
        raise HTTPException(status_code=404, detail="task_not_found")
    row = (await session.execute(text("SELECT * FROM tasks WHERE id = :id"), {"id": task_ref})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="task_not_found")
    return dict(row)


@router.post("/{task_ref}/labels", dependencies=[Depends(require_sensitive_auth)])
async def put_label(
    task_ref: str, body: LabelIn, response: Response, session: AsyncSession = Depends(get_session)
):
    await _task_row(session, task_ref)
    now = _fmt(datetime.now(timezone.utc))
    note = scrub_text(body.note, **features.redaction_options()) if body.note else None
    status = "skipped" if body.skipped else "ok"
    existing = (
        await session.execute(
            text(
                "SELECT id FROM task_evaluations WHERE evaluator = 'human' AND task_ref = :t "
                "AND rubric_version = :r AND labeler = :l"
            ),
            {"t": task_ref, "r": body.rubric_version, "l": body.labeler},
        )
    ).first()
    evaluation_id = existing[0] if existing else str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO task_evaluations (id, task_ref, evaluator, rubric_version, status, label, labeler, note, "
            "http_attempts, evaluated_at) VALUES (:id, :t, 'human', :r, :s, :label, :l, :note, 0, :now) "
            "ON CONFLICT(task_ref, rubric_version, labeler) WHERE evaluator = 'human' DO UPDATE SET "
            "status = excluded.status, label = excluded.label, note = excluded.note, "
            "evaluated_at = excluded.evaluated_at"
        ),
        {"id": evaluation_id, "t": task_ref, "r": body.rubric_version, "s": status,
         "label": body.label, "l": body.labeler, "note": note, "now": now},
    )
    await session.commit()
    response.status_code = 200 if existing else 201
    return {"id": evaluation_id, "task_ref": task_ref, "status": status, "label": body.label,
            "labeler": body.labeler, "rubric_version": body.rubric_version, "evaluated_at": now}


# ---- dynamic detail route: keep last so static /api/tasks/<name> paths win ----


def _evaluation_item(row: dict) -> dict:
    return {
        "id": row["id"],
        "evaluator": row["evaluator"],
        "rubric_version": row["rubric_version"],
        "status": row["status"],
        "label": row["label"],
        "labeler": row["labeler"],
        "raw_score": row["raw_score"],
        "display_score": None if row["raw_score"] is None else row["raw_score"] + 1,
        "confidence": row["confidence"],
        "probabilities": json.loads(row["probabilities_json"]) if row["probabilities_json"] else None,
        "provider_used": row["provider_used"],
        "cost_usd": row["cost_usd"],
        "http_attempts": row["http_attempts"],
        "error_type": row["error_type"],
        "evaluated_at": row["evaluated_at"],
    }


async def get_task_detail(
    task_ref: str,
    include_prompt: bool = False,
    x_ingest_token: Optional[str] = Header(default=None, alias="X-Ingest-Token"),
    origin: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    if include_prompt:
        await require_sensitive_auth(x_ingest_token=x_ingest_token, origin=origin)
    row = await _task_row(session, task_ref)
    (item,) = await build_items(session, [row])
    child_rows = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT id, turn_id, source_task_id, hierarchy_status, start_complexity, first_seen_at "
                    "FROM tasks WHERE parent_task_ref = :id ORDER BY first_seen_at, id LIMIT :limit"
                ),
                {"id": task_ref, "limit": MAX_CHILDREN + 1},
            )
        ).mappings()
    ]
    child_jev = await _latest_jev(session, [c["id"] for c in child_rows[:MAX_CHILDREN]])
    evaluations = [
        _evaluation_item(dict(r))
        for r in (
            await session.execute(
                text("SELECT * FROM task_evaluations WHERE task_ref = :id ORDER BY evaluated_at DESC, id DESC"),
                {"id": task_ref},
            )
        ).mappings()
    ]
    now = _fmt(datetime.now(timezone.utc))
    item.update(
        {
            "children": [
                {
                    "task_ref": c["id"],
                    "turn_id": c["turn_id"],
                    "source_task_id": c["source_task_id"],
                    "hierarchy_status": c["hierarchy_status"],
                    "start_complexity": c["start_complexity"],
                    "jev_raw_score": (child_jev.get(c["id"]) or {}).get("raw_score"),
                    "first_seen_at": c["first_seen_at"],
                }
                for c in child_rows[:MAX_CHILDREN]
            ],
            "children_truncated": len(child_rows) > MAX_CHILDREN,
            "evaluations": evaluations,
            "prompt_length": row["prompt_length"],
            "prompt_truncated": bool(row["prompt_truncated"]),
            "prompt_redaction_version": row["prompt_redaction_version"],
        }
    )
    if include_prompt:
        # read-time expiry enforcement: expired-but-unpurged text is never served
        item["prompt_text"] = row["prompt_text"] if prompt_state(row, now) == "retained" else None
    return item


def register_detail_route() -> None:
    """Called after every static /api/tasks/* route is registered."""
    router.add_api_route("/{task_ref}", get_task_detail, methods=["GET"])
