"""Cost per launcher job (O9 J5): one row per job_ref, read-only, no auth (like /api/analytics/*).

A job is an aggregate, not a table. The job of an event is the job of its task (`tasks.job_ref`, first
valid job wins) when the event belongs to a task, else the event's own normalized `job_ref` tag. Only
counted events contribute: `llm_request`, not evaluator (JEV) usage, not marked `attribution_invalid`.
Filters select whole jobs; the `days` window selects jobs by their last counted event.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_session
from jev_scorer import EVALUATOR_PROJECT
from routes.events import RUNTIMES, WORK_TYPES

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_creation_tokens")
_ESTIMATED = "('partial', 'estimated', 'legacy')"


def _tag(name: str) -> str:
    return f"CASE WHEN json_valid(e.tags_json) THEN json_extract(e.tags_json, '$.{name}') END"


# Counted llm events with their job. Evaluator usage and invalid runtime/producer pairs never count.
_COUNTED = (
    "e.event_type = 'llm_request' "
    "AND NOT (e.project_name = :evaluator_project AND COALESCE(e.role, '') = 'evaluator') "
    f"AND COALESCE({_tag('attribution_invalid')}, 0) = 0"
)
_EVENTS_CTE = f"""
WITH ev AS (
    SELECT e.project_name, e.cost_status, e.estimated_cost_usd,
           e.prompt_tokens, e.completion_tokens, e.cache_read_tokens, e.cache_creation_tokens,
           COALESCE(e.occurred_at, e.recorded_at) AS ts,
           t.id AS task_ref,
           CASE WHEN t.id IS NOT NULL THEN t.job_ref ELSE {_tag('job_ref')} END AS job,
           {_tag('runtime')} AS runtime,
           {_tag('work_type')} AS work_type,
           {_tag('job_attempt')} AS job_attempt
    FROM token_events e
    LEFT JOIN tasks t ON t.project_name = e.project_name AND t.session_id = COALESCE(e.session_id, '')
         AND t.turn_id = e.turn_id
    WHERE {_COUNTED}
)
"""
_JOBS_SQL = _EVENTS_CTE + f"""
SELECT ev.job AS job_ref,
       COUNT(DISTINCT ev.task_ref) AS task_count,
       COUNT(*) AS llm_request_count,
       {", ".join(f"COALESCE(SUM(ev.{n}), 0) AS {n}" for n in _TOKEN_FIELDS)},
       SUM(CASE WHEN ev.cost_status = 'priced' THEN 1 ELSE 0 END) AS priced_count,
       SUM(CASE WHEN ev.cost_status = 'priced' THEN COALESCE(ev.estimated_cost_usd, 0) ELSE 0 END) AS priced_sum,
       SUM(CASE WHEN ev.cost_status = 'unpriced' THEN 1 ELSE 0 END) AS unpriced_count,
       SUM(CASE WHEN ev.cost_status IN {_ESTIMATED} THEN 1 ELSE 0 END) AS estimated_count,
       SUM(CASE WHEN ev.cost_status IN {_ESTIMATED} THEN COALESCE(ev.estimated_cost_usd, 0) ELSE 0 END)
           AS estimated_sum,
       MIN(ev.ts) AS first_event_at,
       MAX(ev.ts) AS last_event_at,
       group_concat(DISTINCT ev.runtime) AS runtimes,
       group_concat(DISTINCT ev.work_type) AS work_types,
       group_concat(DISTINCT ev.job_attempt) AS attempts,
       group_concat(DISTINCT ev.project_name) AS projects,
       COALESCE(c.conflict_count, 0) AS conflict_count,
       COALESCE(c.conflict_task_count, 0) AS conflict_task_count
FROM ev
LEFT JOIN (
    -- conflicts per job from tasks alone: never summed after the event join (it would multiply them)
    SELECT job_ref, SUM(job_ref_conflicts) AS conflict_count,
           SUM(CASE WHEN job_ref_conflicts > 0 THEN 1 ELSE 0 END) AS conflict_task_count
    FROM tasks WHERE job_ref IS NOT NULL GROUP BY job_ref
) c ON c.job_ref = ev.job
WHERE ev.job IS NOT NULL
GROUP BY ev.job
HAVING MAX(ev.ts) >= :cutoff
ORDER BY last_event_at DESC, job_ref ASC
"""
_ANOMALY_SQL = _EVENTS_CTE + """
SELECT COALESCE(SUM(t.job_ref_conflicts), 0), COALESCE(SUM(CASE WHEN t.job_ref_conflicts > 0 THEN 1 ELSE 0 END), 0)
FROM tasks t WHERE t.id IN (SELECT task_ref FROM ev WHERE task_ref IS NOT NULL AND ts >= :cutoff)
"""
_INVALID_SQL = (
    "SELECT COUNT(*) FROM token_events e WHERE e.event_type = 'llm_request' "
    "AND NOT (e.project_name = :evaluator_project AND COALESCE(e.role, '') = 'evaluator') "
    f"AND COALESCE({_tag('attribution_invalid')}, 0) <> 0 AND COALESCE(e.occurred_at, e.recorded_at) >= :cutoff"
)


def _split(value: Any) -> list[str]:
    return sorted({part for part in str(value).split(",") if part}) if value else []


def _attempts(value: Any) -> list[int]:
    return sorted({int(part) for part in _split(value) if part.isdigit()})


def _item(row) -> dict[str, Any]:
    work_types = _split(row["work_types"])
    priced_count = int(row["priced_count"] or 0)
    unpriced_count = int(row["unpriced_count"] or 0)
    llm_count = int(row["llm_request_count"] or 0)
    return {
        "job_ref": row["job_ref"],
        "runtimes": _split(row["runtimes"]),
        "work_type": work_types[0] if len(work_types) == 1 else None,
        "work_types": work_types,
        "attempts": _attempts(row["attempts"]),
        "projects": _split(row["projects"]),
        "task_count": int(row["task_count"] or 0),
        "llm_request_count": llm_count,
        **{name: int(row[name] or 0) for name in _TOKEN_FIELDS},
        "priced_count": priced_count,
        "unpriced_count": unpriced_count,
        # priced calls only; null when none is priced (an unpriced job is never zero or free)
        "cost_usd": float(row["priced_sum"] or 0.0) if priced_count > 0 else None,
        "estimated_cost_usd": float(row["estimated_sum"] or 0.0),
        "cost_complete": unpriced_count == 0 and llm_count > 0 and int(row["estimated_count"] or 0) == 0,
        "first_event_at": row["first_event_at"],
        "last_event_at": row["last_event_at"],
        "conflict_count": int(row["conflict_count"] or 0),
        "conflict_task_count": int(row["conflict_task_count"] or 0),
    }


def _invalid_filter() -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "invalid_filter"})


@router.get("")
async def list_jobs(
    days: int = Query(default=30, ge=1, le=3650),
    runtime: Optional[str] = None,
    work_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    if (runtime and runtime not in RUNTIMES) or (work_type and work_type not in WORK_TYPES):
        return _invalid_filter()  # static body: the value is never echoed
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    params = {"cutoff": cutoff, "evaluator_project": EVALUATOR_PROJECT}
    jobs = [_item(row) for row in (await session.execute(text(_JOBS_SQL), params)).mappings().all()]

    # Filters keep whole jobs (their totals are never reduced to the matching calls); each filter's
    # option list ignores that filter itself.
    def keep(job: dict, *, by_runtime: bool = True, by_work_type: bool = True) -> bool:
        return (not (by_runtime and runtime) or runtime in job["runtimes"]) and (
            not (by_work_type and work_type) or work_type in job["work_types"]
        )

    options = {
        "runtimes": sorted({r for job in jobs if keep(job, by_runtime=False) for r in job["runtimes"]}),
        "work_types": sorted({w for job in jobs if keep(job, by_work_type=False) for w in job["work_types"]}),
    }
    selected = [job for job in jobs if keep(job)]
    conflicts, conflict_tasks = (await session.execute(text(_ANOMALY_SQL), params)).one()
    invalid = (await session.execute(text(_INVALID_SQL), params)).scalar_one()
    start = (page - 1) * page_size
    return {
        "items": selected[start:start + page_size],
        "total": len(selected),
        "page": page,
        "page_size": page_size,
        "filters": options,
        "anomalies": {
            "job_ref_conflicts": int(conflicts or 0),
            "job_ref_conflict_tasks": int(conflict_tasks or 0),
            "invalid_attribution_events": int(invalid or 0),
        },
    }
