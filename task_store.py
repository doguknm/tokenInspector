"""Task derivation shared by the schema-v10 backfill and live ingest.

A task is one Hermes turn: one user prompt -> final response, keyed by
(project_name, session_id, turn_id). Hermes' own task_id is session-scoped on the
gateway and API platforms, so it is kept only as `source_task_id`
(Plans/task-telemetry-jev-pilot/status.md, Drift Log, D0).

Every function takes `db`, anything with `await db.execute(text(...), params)`:
an AsyncConnection inside a migration or an AsyncSession during ingest.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from redaction import REDACTION_VERSION, scrub_text

APPROVED_METHODS = frozenset({"request-shape-v1"})
SESSION_END_REASONS = ("end", "shutdown", "session_boundary", "new_session")
_INVALID_TURN_IDS = frozenset({"", "unknown", "session"})
_TS = "COALESCE(occurred_at, recorded_at)"
_VALID_TURN_SQL = (
    "event_type <> 'session' AND turn_id IS NOT NULL AND turn_id NOT IN ('', 'unknown', 'session')"
)


def task_ref(project_name: str, session_id: Optional[str], turn_id: str) -> str:
    raw = "\x1f".join((project_name, session_id or "", turn_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def is_task_turn(event_type: Optional[str], turn_id: Optional[str]) -> bool:
    return event_type != "session" and bool(turn_id) and turn_id not in _INVALID_TURN_IDS


def _tags(tags_json: Any) -> dict:
    if isinstance(tags_json, dict):
        return tags_json
    if not tags_json:
        return {}
    try:
        value = json.loads(tags_json)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def complexity_method_of(complexity_method: Optional[str], tags_json: Any) -> Optional[str]:
    """Provenance of a complexity value: the column, else the tags (D0: `complexity_version`)."""
    if complexity_method:
        return complexity_method
    tags = _tags(tags_json)
    value = tags.get("complexity_method") or tags.get("complexity_version")
    return str(value) if value else None


def start_complexity_candidate(
    event_type: Optional[str],
    complexity: Optional[int],
    complexity_method: Optional[str],
    tags_json: Any,
) -> Optional[str]:
    """The one candidate rule for backfill and ingest; returns the method to store, or None."""
    if event_type != "llm_request" or complexity is None:
        return None
    if complexity_method_of(complexity_method, tags_json) in APPROVED_METHODS:
        return "request-shape-v1"
    return None


async def recompute_session_completion(db, project_name: str, session_id: str, now: str) -> int:
    """Recompute completion for every task of one project+session from stored rows only.

    Independent of arrival order: a late older task still gets `next_task`, and a
    session-end marker that arrived before its tasks still marks the last one.
    Returns the number of rows changed.
    """
    if not session_id:
        return 0
    tasks = (
        await db.execute(
            text(
                "SELECT id, first_seen_at, completion, completed_at FROM tasks "
                "WHERE project_name = :p AND session_id = :s ORDER BY first_seen_at, id"
            ),
            {"p": project_name, "s": session_id},
        )
    ).all()
    if not tasks:
        return 0
    placeholders = ", ".join(f":r{i}" for i in range(len(SESSION_END_REASONS)))
    marker = (
        await db.execute(
            text(
                f"SELECT MIN({_TS}) FROM token_events "
                "WHERE project_name = :p AND session_id = :s AND event_type = 'session' "
                f"AND finish_reason IN ({placeholders}) AND {_TS} >= :first"
            ),
            {
                "p": project_name,
                "s": session_id,
                "first": tasks[-1][1],
                **{f"r{i}": reason for i, reason in enumerate(SESSION_END_REASONS)},
            },
        )
    ).scalar()
    changed = 0
    for index, (ref, _first, completion, completed_at) in enumerate(tasks):
        if index < len(tasks) - 1:
            wanted = ("next_task", tasks[index + 1][1])
        elif marker:
            wanted = ("session_end", marker)
        else:
            wanted = (None, None)
        if (completion, completed_at) != wanted:
            await db.execute(
                text("UPDATE tasks SET completion = :c, completed_at = :at, updated_at = :now WHERE id = :id"),
                {"c": wanted[0], "at": wanted[1], "now": now, "id": ref},
            )
            changed += 1
    return changed


async def backfill_tasks(db, now: str) -> int:
    """Metadata-only backfill of `tasks` from existing token_events (idempotent)."""
    groups = (
        await db.execute(
            text(
                "SELECT project_name, COALESCE(session_id, '') AS s, turn_id, "
                f"MIN(NULLIF(task_id, '')), MIN({_TS}), MAX({_TS}) FROM token_events "
                f"WHERE {_VALID_TURN_SQL} GROUP BY project_name, s, turn_id"
            )
        )
    ).all()
    starts: dict[tuple[str, str, str], tuple] = {}
    rows = (
        await db.execute(
            text(
                "SELECT project_name, COALESCE(session_id, '') AS s, turn_id, id, event_type, "
                f"complexity, complexity_method, tags_json, {_TS} AS ts FROM token_events "
                f"WHERE {_VALID_TURN_SQL} AND event_type = 'llm_request' AND complexity IS NOT NULL "
                "ORDER BY project_name, s, turn_id, ts, id"
            )
        )
    ).all()
    for project, session, turn, event_id, event_type, complexity, method, tags, ts in rows:
        key = (project, session, turn)
        if key in starts:
            continue
        chosen = start_complexity_candidate(event_type, complexity, method, tags)
        if chosen:
            starts[key] = (complexity, chosen, ts, event_id)

    inserted = 0
    for project, session, turn, source_task_id, first_seen, last_seen in groups:
        complexity, method, event_at, event_id = starts.get((project, session, turn), (None, None, None, None))
        result = await db.execute(
            text(
                "INSERT OR IGNORE INTO tasks (id, project_name, session_id, turn_id, source_task_id, "
                "hierarchy_status, source, first_seen_at, last_seen_at, start_complexity, "
                "start_complexity_method, start_complexity_event_at, start_complexity_event_id, "
                "prompt_truncated, created_at, updated_at) VALUES (:id, :p, :s, :t, :src, 'unknown', "
                "'backfill', :first, :last, :c, :m, :at, :eid, 0, :now, :now)"
            ),
            {
                "id": task_ref(project, session, turn),
                "p": project,
                "s": session,
                "t": turn,
                "src": source_task_id,
                "first": first_seen,
                "last": last_seen,
                "c": complexity,
                "m": method,
                "at": event_at,
                "eid": event_id,
                "now": now,
            },
        )
        inserted += result.rowcount or 0

    sessions = {(project, session) for project, session, *_ in groups if session}
    for project, session in sorted(sessions):
        await recompute_session_completion(db, project, session, now)
    return inserted


PROMPT_RETENTION = timedelta(days=30)
PROMPT_STORE_MAX_CHARS = 32_000
FUTURE_SKEW = timedelta(minutes=5)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _upsert_task(db, event, body, session_id: str, now: str) -> str:
    project = event.project_name
    ref = task_ref(project, session_id, event.turn_id)
    ts = event.occurred_at or event.recorded_at
    parent_ref = root_ref = None
    if body.parent_session_id and body.parent_turn_id:
        parent_ref = task_ref(project, body.parent_session_id, body.parent_turn_id)
        parent = (
            await db.execute(text("SELECT root_task_ref FROM tasks WHERE id = :id"), {"id": parent_ref})
        ).first()
        root_ref = (parent[0] if parent and parent[0] else parent_ref)
    if parent_ref or body.task_hierarchy == "child":
        hierarchy = "child"
    elif body.task_hierarchy == "root":
        hierarchy = "root"
    else:
        hierarchy = "unknown"
    await db.execute(
        text(
            "INSERT INTO tasks (id, project_name, session_id, turn_id, source_task_id, parent_task_ref, "
            "root_task_ref, hierarchy_status, source, first_seen_at, last_seen_at, prompt_truncated, "
            "created_at, updated_at) VALUES (:id, :p, :s, :t, :src, :parent, :root, :h, 'ingest', :ts, :ts, 0, "
            ":now, :now) "
            "ON CONFLICT(project_name, session_id, turn_id) DO UPDATE SET "
            "first_seen_at = MIN(tasks.first_seen_at, excluded.first_seen_at), "
            "last_seen_at = MAX(tasks.last_seen_at, excluded.last_seen_at), "
            "source_task_id = COALESCE(tasks.source_task_id, excluded.source_task_id), "
            "parent_task_ref = COALESCE(tasks.parent_task_ref, excluded.parent_task_ref), "
            "root_task_ref = COALESCE(tasks.root_task_ref, excluded.root_task_ref), "
            # child is never downgraded
            "hierarchy_status = CASE WHEN tasks.hierarchy_status = 'child' OR excluded.hierarchy_status = 'child' "
            "THEN 'child' WHEN excluded.hierarchy_status = 'root' THEN 'root' ELSE tasks.hierarchy_status END, "
            "updated_at = excluded.updated_at"
        ),
        {
            "id": ref, "p": project, "s": session_id, "t": event.turn_id, "src": event.task_id or None,
            "parent": parent_ref, "root": root_ref, "h": hierarchy, "ts": ts, "now": now,
        },
    )
    if parent_ref:
        # Descendants that arrived first used this task as a provisional root; re-root them.
        await db.execute(
            text(
                "UPDATE tasks SET root_task_ref = (SELECT COALESCE(root_task_ref, parent_task_ref) FROM tasks WHERE id = :id), "
                "updated_at = :now WHERE root_task_ref = :id AND project_name = :p"
            ),
            {"id": ref, "p": project, "now": now},
        )
    chosen = start_complexity_candidate(event.event_type, event.complexity, event.complexity_method, event.tags_json)
    if chosen:
        await db.execute(
            text(
                "UPDATE tasks SET start_complexity = :c, start_complexity_method = :m, "
                "start_complexity_event_at = :at, start_complexity_event_id = :eid, updated_at = :now "
                "WHERE id = :id AND (start_complexity_event_at IS NULL OR :at < start_complexity_event_at "
                "OR (:at = start_complexity_event_at AND :eid < start_complexity_event_id))"
            ),
            {"c": event.complexity, "m": chosen, "at": ts, "eid": event.id, "now": now, "id": ref},
        )
    return ref


async def _store_prompt(db, ref: str, body, event, now_dt: datetime, redaction: dict) -> bool:
    """First write wins; expiry comes from producer capture time and is never extended."""
    captured = _parse(body.task_prompt_captured_at or event.occurred_at or event.recorded_at)
    if captured > now_dt + FUTURE_SKEW:
        captured = now_dt
    if captured + PROMPT_RETENTION <= now_dt:
        return False  # an old spool replay never gets a fresh retention period
    original = body.task_prompt_text
    redacted = scrub_text(original, **redaction)
    truncated = len(redacted) > PROMPT_STORE_MAX_CHARS
    redacted = redacted[:PROMPT_STORE_MAX_CHARS]
    result = await db.execute(
        text(
            "UPDATE tasks SET prompt_text = :text, prompt_hash = :hash, prompt_length = :length, "
            "prompt_truncated = :truncated, prompt_redaction_version = :version, "
            "prompt_captured_at = :captured, prompt_expires_at = :expires, updated_at = :now "
            "WHERE id = :id AND prompt_captured_at IS NULL"
        ),
        {
            "text": redacted,
            "hash": hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
            "length": len(original),
            "truncated": int(truncated),
            "version": REDACTION_VERSION,
            "captured": _fmt(captured),
            "expires": _fmt(captured + PROMPT_RETENTION),
            "now": _fmt(now_dt),
            "id": ref,
        },
    )
    return bool(result.rowcount)


async def on_event_inserted(db, event, body, *, capture_enabled: bool, redaction: dict) -> dict:
    """Task derivation for one newly inserted (non-duplicate) event, in the ingest transaction."""
    now_dt = datetime.now(timezone.utc)
    now = _fmt(now_dt)
    session_id = event.session_id or ""
    outcome = {"task_ref": None, "prompt": "none"}
    if is_task_turn(event.event_type, event.turn_id):
        ref = await _upsert_task(db, event, body, session_id, now)
        outcome["task_ref"] = ref
        if body.task_prompt_text:
            if not capture_enabled:
                outcome["prompt"] = "discarded_disabled"
            elif await _store_prompt(db, ref, body, event, now_dt, redaction):
                outcome["prompt"] = "stored"
            else:
                outcome["prompt"] = "not_stored"
    elif body.task_prompt_text:
        outcome["prompt"] = "discarded_no_task"
    if session_id:
        await recompute_session_completion(db, event.project_name, session_id, now)
    return outcome
