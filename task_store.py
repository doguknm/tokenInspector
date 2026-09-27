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
from typing import Any, Optional

from sqlalchemy import text

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
