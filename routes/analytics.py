from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_session
from models import TokenEvent
from project_inventory import discover_projects
from routes.events import RUNTIMES, _clean_agent
from routes.tasks import _completion

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _cutoff(days: int) -> str:
    days = min(max(days, 1), 3650)
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _cost_columns():
    priced = func.coalesce(
        func.sum(case((TokenEvent.cost_status == "priced", TokenEvent.estimated_cost_usd), else_=0.0)),
        0.0,
    )
    estimated = func.coalesce(
        func.sum(
            case(
                (TokenEvent.cost_status.in_(["partial", "estimated", "legacy"]), TokenEvent.estimated_cost_usd),
                else_=0.0,
            )
        ),
        0.0,
    )
    unpriced = func.sum(case((TokenEvent.cost_status == "unpriced", 1), else_=0))
    return priced, estimated, unpriced


def _llm_filter(days: int):
    return (TokenEvent.recorded_at >= _cutoff(days), TokenEvent.event_type == "llm_request")


# Per-category token sums, kept separate: prompt_tokens excludes cache for every producer
# (ingest subtracts cache when input_tokens_include_cache is set).
_TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_creation_tokens")


def _token_sum_columns():
    return [func.coalesce(func.sum(getattr(TokenEvent, name)), 0).label(name) for name in _TOKEN_FIELDS]


def _token_sums(row) -> dict:
    return {name: int(row[name] or 0) for name in _TOKEN_FIELDS}


_AGENT_METHODS = ("request-shape-v1", "cc-input-size-v1")
_ESTIMATED_COST = ("partial", "estimated", "legacy")
AGENT_CELL_MAX = 20000


def _iso(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _agent_bucket() -> dict[str, Any]:
    return {"calls": 0, "tasks": set(), "calls_without_task": 0,
            **dict.fromkeys(_TOKEN_FIELDS, 0), "priced_calls": 0, "unpriced_calls": 0,
            "estimated_calls": 0, "priced_cost": 0.0, "estimated_cost": 0.0,
            "latency_calls": 0, "latency_sum": 0, "ttft_calls": 0, "ttft_sum": 0}


def _agent_add(bucket: dict, row, task_key) -> None:
    bucket["calls"] += 1
    if task_key is None:
        bucket["calls_without_task"] += 1
    else:
        bucket["tasks"].add(task_key)
    for name in _TOKEN_FIELDS:
        bucket[name] += int(row[name] or 0)
    status = row["cost_status"]
    if status == "priced":
        bucket["priced_calls"] += 1
        bucket["priced_cost"] += float(row["estimated_cost_usd"] or 0)
    elif status == "unpriced":
        bucket["unpriced_calls"] += 1
    elif status in _ESTIMATED_COST:
        bucket["estimated_calls"] += 1
        bucket["estimated_cost"] += float(row["estimated_cost_usd"] or 0)
    if row["process_time_ms"] is not None:
        bucket["latency_calls"] += 1
        bucket["latency_sum"] += int(row["process_time_ms"])
    if row["ttft_ms"] is not None:
        bucket["ttft_calls"] += 1
        bucket["ttft_sum"] += int(row["ttft_ms"])


def _agent_finish(bucket: dict) -> dict[str, Any]:
    priced = bucket["priced_calls"]
    return {"calls": bucket["calls"], "tasks": len(bucket["tasks"]),
            "calls_without_task": bucket["calls_without_task"],
            **{name: bucket[name] for name in _TOKEN_FIELDS},
            "priced_calls": priced, "unpriced_calls": bucket["unpriced_calls"],
            "estimated_calls": bucket["estimated_calls"],
            "priced_cost_usd": round(bucket["priced_cost"], 6) if priced else None,
            "estimated_cost_usd": round(bucket["estimated_cost"], 6),
            "cost_complete": bucket["calls"] > 0 and priced == bucket["calls"],
            "latency_calls": bucket["latency_calls"],
            "avg_process_time_ms": round(bucket["latency_sum"] / bucket["latency_calls"])
            if bucket["latency_calls"] else None,
            "ttft_calls": bucket["ttft_calls"],
            "avg_ttft_ms": round(bucket["ttft_sum"] / bucket["ttft_calls"])
            if bucket["ttft_calls"] else None}


@router.get("/summary")
async def summary(days: int = 7, session: AsyncSession = Depends(get_session)):
    priced, estimated, unpriced = _cost_columns()
    query = select(
        func.count(TokenEvent.id).label("total_events"),
        func.coalesce(func.sum(TokenEvent.prompt_tokens), 0).label("prompt"),
        func.coalesce(func.sum(TokenEvent.completion_tokens), 0).label("completion"),
        func.coalesce(func.sum(TokenEvent.cache_read_tokens), 0).label("cache_read"),
        func.coalesce(func.sum(TokenEvent.cache_creation_tokens), 0).label("cache_creation"),
        func.coalesce(func.sum(TokenEvent.reasoning_tokens), 0).label("reasoning"),
        priced.label("priced_cost"),
        estimated.label("estimated_cost"),
        unpriced.label("unpriced_count"),
        func.count(func.distinct(TokenEvent.project_name)).label("projects"),
        func.count(func.distinct(TokenEvent.model)).label("models"),
        func.count(func.distinct(TokenEvent.provider)).label("providers"),
        func.count(func.distinct(TokenEvent.tool_name)).label("tools"),
        func.avg(TokenEvent.process_time_ms).label("avg_latency"),
        func.avg(TokenEvent.ttft_ms).label("avg_ttft"),
        func.sum(case((TokenEvent.status == "error", 1), else_=0)).label("errors"),
    ).where(*_llm_filter(days))
    row = (await session.execute(query)).mappings().one()
    total = int(row["total_events"] or 0)
    return {
        "total_events": total,
        "total_prompt_tokens": int(row["prompt"] or 0),
        "total_completion_tokens": int(row["completion"] or 0),
        "total_cache_read_tokens": int(row["cache_read"] or 0),
        "total_cache_creation_tokens": int(row["cache_creation"] or 0),
        "total_reasoning_tokens": int(row["reasoning"] or 0),
        "total_cost_usd": round(float(row["priced_cost"] or 0), 6),
        "estimated_cost_usd": round(float(row["estimated_cost"] or 0), 6),
        "unpriced_event_count": int(row["unpriced_count"] or 0),
        "unique_projects": int(row["projects"] or 0),
        "unique_models": int(row["models"] or 0),
        "unique_providers": int(row["providers"] or 0),
        "unique_tools": int(row["tools"] or 0),
        "avg_process_time_ms": round(float(row["avg_latency"])) if row["avg_latency"] is not None else None,
        "avg_ttft_ms": round(float(row["avg_ttft"])) if row["avg_ttft"] is not None else None,
        "error_rate": round(int(row["errors"] or 0) / total, 6) if total else 0.0,
    }


async def _grouped(
    session: AsyncSession,
    *,
    field,
    label: str,
    days: int,
    project: Optional[str] = None,
    include_non_llm: bool = False,
):
    priced, estimated, unpriced = _cost_columns()
    conditions = [TokenEvent.recorded_at >= _cutoff(days)]
    if not include_non_llm:
        conditions.append(TokenEvent.event_type == "llm_request")
    if project:
        conditions.append(TokenEvent.project_name == project)
    query = (
        select(
            field.label(label),
            func.count(TokenEvent.id).label("event_count"),
            func.coalesce(func.sum(TokenEvent.prompt_tokens + TokenEvent.completion_tokens), 0).label("total_tokens"),
            func.avg(TokenEvent.prompt_tokens).label("avg_prompt_tokens"),
            func.avg(TokenEvent.completion_tokens).label("avg_completion_tokens"),
            func.avg(TokenEvent.process_time_ms).label("avg_process_time_ms"),
            *_token_sum_columns(),
            priced.label("total_cost_usd"),
            estimated.label("estimated_cost_usd"),
            unpriced.label("unpriced_event_count"),
        )
        .where(*conditions)
        .group_by(field)
        .order_by(priced.desc())
    )
    rows = (await session.execute(query)).mappings().all()
    result = []
    for row in rows:
        total_tokens = int(row["total_tokens"] or 0)
        total_cost = float(row["total_cost_usd"] or 0)
        item = {
            label: row[label] if row[label] is not None else "unknown",
            "event_count": int(row["event_count"] or 0),
            "total_tokens": total_tokens,
            "avg_prompt_tokens": round(float(row["avg_prompt_tokens"] or 0)),
            "avg_completion_tokens": round(float(row["avg_completion_tokens"] or 0)),
            "avg_process_time_ms": round(float(row["avg_process_time_ms"])) if row["avg_process_time_ms"] is not None else None,
            "total_cost_usd": round(total_cost, 6),
            "estimated_cost_usd": round(float(row["estimated_cost_usd"] or 0), 6),
            "unpriced_event_count": int(row["unpriced_event_count"] or 0),
            "cost_per_1k_tokens": round(total_cost / total_tokens * 1000, 6) if total_tokens else None,
            **_token_sums(row),
        }
        result.append(item)
    return result


@router.get("/by-project")
async def by_project(days: int = 30, session: AsyncSession = Depends(get_session)):
    return await _grouped(session, field=TokenEvent.project_name, label="project_name", days=days)


@router.get("/project-inventory")
async def project_inventory(days: int = 30, session: AsyncSession = Depends(get_session)):
    projects = await asyncio.to_thread(discover_projects)
    query = (
        select(
            TokenEvent.project_name,
            func.count(TokenEvent.id).label("event_count"),
            func.coalesce(func.sum(TokenEvent.prompt_tokens + TokenEvent.completion_tokens), 0).label("total_tokens"),
            func.max(TokenEvent.recorded_at).label("last_activity_at"),
            *_token_sum_columns(),
        )
        .where(*_llm_filter(days))
        .group_by(TokenEvent.project_name)
    )
    activity_rows = (await session.execute(query)).mappings().all()
    activity = {row["project_name"]: row for row in activity_rows}
    result = []
    for project in projects:
        row = activity.get(project["project_name"])
        result.append(
            {
                **project,
                "has_telemetry": row is not None,
                "event_count": int(row["event_count"] or 0) if row else 0,
                "total_tokens": int(row["total_tokens"] or 0) if row else 0,
                "last_activity_at": row["last_activity_at"] if row else None,
                **(_token_sums(row) if row else dict.fromkeys(_TOKEN_FIELDS, 0)),
            }
        )
    return result


@router.get("/by-model")
async def by_model(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    return await _grouped(session, field=TokenEvent.model, label="model", days=days, project=project)


@router.get("/by-provider")
async def by_provider(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    return await _grouped(session, field=TokenEvent.provider, label="provider", days=days, project=project)


@router.get("/by-tool")
async def by_tool(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    return await _grouped(
        session,
        field=TokenEvent.tool_name,
        label="tool_name",
        days=days,
        project=project,
        include_non_llm=True,
    )


@router.get("/by-role")
async def by_role(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    return await _grouped(session, field=TokenEvent.role, label="role", days=days, project=project)


@router.get("/by-session")
async def by_session(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    return await _grouped(session, field=TokenEvent.session_id, label="session_id", days=days, project=project)


@router.get("/timeseries")
async def timeseries(
    days: int = 30,
    granularity: str = "day",
    project: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    if granularity not in {"day", "hour"}:
        raise HTTPException(status_code=422, detail="granularity must be day or hour")
    length = 10 if granularity == "day" else 13
    bucket = func.substr(TokenEvent.recorded_at, 1, length)
    priced, estimated, unpriced = _cost_columns()
    conditions = list(_llm_filter(days))
    if project:
        conditions.append(TokenEvent.project_name == project)
    query = (
        select(
            bucket.label("date"),
            func.coalesce(func.sum(TokenEvent.prompt_tokens + TokenEvent.completion_tokens), 0).label("total_tokens"),
            func.count(TokenEvent.id).label("event_count"),
            *_token_sum_columns(),
            priced.label("total_cost_usd"),
            estimated.label("estimated_cost_usd"),
            unpriced.label("unpriced_event_count"),
        )
        .where(*conditions)
        .group_by(bucket)
        .order_by(bucket)
    )
    rows = (await session.execute(query)).mappings().all()
    return [
        {
            "date": row["date"],
            "total_tokens": int(row["total_tokens"] or 0),
            "event_count": int(row["event_count"] or 0),
            "total_cost_usd": round(float(row["total_cost_usd"] or 0), 6),
            "estimated_cost_usd": round(float(row["estimated_cost_usd"] or 0), 6),
            "unpriced_event_count": int(row["unpriced_event_count"] or 0),
            **_token_sums(row),
        }
        for row in rows
    ]


@router.get("/agents")
async def agents(
    start: str = Query(alias="from"), end: str = Query(alias="to"), project: Optional[str] = None,
    runtime: list[str] = Query(default=[]), agent: list[str] = Query(default=[]), agent_missing: bool = False,
    method: list[str] = Query(default=[]), complexity: list[str] = Query(default=[]),
    include_unscored: bool = True, model: list[str] = Query(default=[]),
    session: AsyncSession = Depends(get_session),
):
    """Deploy-onward calls grouped by runtime, sanitized agent, method, tier, model and UTC day."""
    start_dt, end_dt = _iso(start), _iso(end)
    filters = (runtime, agent, method, complexity, model)
    if (start_dt is None or end_dt is None or start_dt >= end_dt or end_dt - start_dt > timedelta(days=92)
            or any(len(values) > 100 for values in filters) or (agent_missing and agent)):
        raise HTTPException(status_code=400, detail="invalid_filter")
    if project is not None and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", project):
        raise HTTPException(status_code=400, detail="invalid_filter")
    if any(value not in RUNTIMES for value in runtime) or any(value not in _AGENT_METHODS for value in method):
        raise HTTPException(status_code=400, detail="invalid_filter")
    if any(_clean_agent(value) != value for value in agent):
        raise HTTPException(status_code=400, detail="invalid_filter")
    try:
        tiers = [int(value) for value in complexity]
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_filter") from None
    if any(tier < 1 or tier > 5 for tier in tiers):
        raise HTTPException(status_code=400, detail="invalid_filter")
    ts = "COALESCE(e.occurred_at, e.recorded_at)"
    runtime_sql = "COALESCE(json_extract(e.tags_json, '$.runtime'), 'hermes-agent')"
    where = [f"{ts} >= :start", f"{ts} < :end", "e.event_type = 'llm_request'",
             "NOT (e.project_name = 'token-inspector' AND COALESCE(e.role, '') = 'evaluator')",
             "COALESCE(json_extract(e.tags_json, '$.attribution_invalid'), 0) = 0"]
    params: dict[str, Any] = {"start": _fmt(start_dt), "end": _fmt(end_dt)}

    def repeated(column: str, values: list, prefix: str) -> None:
        if values:
            names = []
            for index, value in enumerate(values):
                name = f"{prefix}{index}"
                params[name] = value
                names.append(":" + name)
            where.append(f"{column} IN ({', '.join(names)})")

    if project is not None:
        where.append("e.project_name = :project")
        params["project"] = project
    repeated(runtime_sql, runtime, "r")
    repeated("e.agent", agent, "a")
    if agent_missing:
        where.append("e.agent IS NULL")
    repeated("e.complexity_method", method, "mth")
    repeated("e.complexity", tiers, "c")
    repeated("e.model", model, "mdl")
    if not include_unscored:
        where.append("e.complexity IS NOT NULL")
    columns = ", ".join(["e.project_name", "e.session_id", "e.turn_id", "e.agent", "e.complexity_method",
                         "e.complexity", "e.model", "e.cost_status", "e.estimated_cost_usd",
                         "e.process_time_ms", "e.ttft_ms", *[f"e.{name}" for name in _TOKEN_FIELDS]])
    rows = (await session.execute(text(
        f"SELECT {columns}, {runtime_sql} AS runtime, {ts} AS ts FROM token_events e "
        f"WHERE {' AND '.join(where)} ORDER BY e.rowid"
    ), params)).mappings().all()
    buckets: dict[tuple, dict] = {}
    totals = _agent_bucket()
    coverage = dict.fromkeys(("agent_unavailable_calls", "agent_unknown_calls", "agent_custom_calls",
                              "complexity_unscored_calls"), 0)
    methods, global_tasks = set(), set()
    for row in rows:
        key = (row["runtime"], row["agent"], row["complexity_method"], row["complexity"], row["model"], row["ts"][:10])
        if key not in buckets and len(buckets) >= AGENT_CELL_MAX:
            raise HTTPException(status_code=413, detail="result_too_large")
        task_key = ((row["project_name"], row["session_id"] or "", row["turn_id"])
                    if row["turn_id"] not in (None, "", "unknown", "session") else None)
        if task_key is not None:
            global_tasks.add(task_key)
        _agent_add(buckets.setdefault(key, _agent_bucket()), row, task_key)
        _agent_add(totals, row, task_key)
        coverage["agent_unavailable_calls"] += row["agent"] is None
        coverage["agent_unknown_calls"] += row["agent"] == "unknown"
        coverage["agent_custom_calls"] += row["agent"] == "custom"
        coverage["complexity_unscored_calls"] += row["complexity"] is None
        if row["complexity_method"] is not None:
            methods.add(row["complexity_method"])
    cells = []
    for key, bucket in buckets.items():
        runtime_value, agent_value, method_value, tier, model_value, day = key
        cells.append({"runtime": runtime_value, "agent": agent_value, "complexity_method": method_value,
                      "complexity": tier, "model": model_value, "day": day, **_agent_finish(bucket)})
    cells.sort(key=lambda item: tuple("" if item[name] is None else str(item[name]) for name in
                                     ("day", "runtime", "agent", "complexity_method", "complexity", "model")))
    total_item = _agent_finish(totals)
    total_item["tasks"] = len(global_tasks)
    return {"contract_version": 1, "period": {"from": _fmt(start_dt), "to": _fmt(end_dt)},
            "time_basis": "COALESCE(occurred_at, recorded_at), UTC calendar day", "tier_source": "llm_call",
            "task_count_semantics": "global distinct project/session/turn; cells count distinct contributors",
            "methods": sorted(methods), "coverage": coverage, "totals": total_item, "cells": cells,
            "complete": True}


@router.get("/by-complexity")
async def by_complexity(days: int = 30, project: Optional[str] = None, method: Optional[str] = None,
                        session: AsyncSession = Depends(get_session)):
    conditions = [*_llm_filter(days), TokenEvent.complexity.is_not(None)]
    if project:
        conditions.append(TokenEvent.project_name == project)
    if method:
        conditions.append(TokenEvent.complexity_method == method)
    query = (
        select(
            TokenEvent.complexity,
            func.avg(
                case(
                    (TokenEvent.cost_status == "priced", TokenEvent.estimated_cost_usd),
                    else_=None,
                )
            ).label("avg_cost_usd"),
            func.avg(TokenEvent.prompt_tokens + TokenEvent.completion_tokens).label("avg_tokens"),
            func.count(TokenEvent.id).label("event_count"),
            func.sum(case((TokenEvent.cost_status == "priced", 1), else_=0)).label("priced_event_count"),
        )
        .where(*conditions)
        .group_by(TokenEvent.complexity)
        .order_by(TokenEvent.complexity)
    )
    rows = (await session.execute(query)).mappings().all()
    return [
        {
            "complexity": row["complexity"],
            "avg_cost_usd": round(float(row["avg_cost_usd"]), 6) if row["avg_cost_usd"] is not None else None,
            "avg_tokens": round(float(row["avg_tokens"])),
            "event_count": int(row["event_count"]),
            "priced_event_count": int(row["priced_event_count"] or 0),
        }
        for row in rows
    ]


@router.get("/routing-recommendations")
async def routing_recommendations(days: int = 30, project: Optional[str] = None, method: Optional[str] = None,
                                  session: AsyncSession = Depends(get_session)):
    conditions = [
        *_llm_filter(days),
        TokenEvent.complexity.is_not(None),
        TokenEvent.role.is_not(None),
        TokenEvent.cost_status == "priced",
    ]
    if project:
        conditions.append(TokenEvent.project_name == project)
    if method:
        conditions.append(TokenEvent.complexity_method == method)
    query = (
        select(
            TokenEvent.role,
            TokenEvent.complexity,
            TokenEvent.model,
            func.count(TokenEvent.id).label("n"),
            func.avg(TokenEvent.estimated_cost_usd).label("avg_cost"),
        )
        .where(*conditions)
        .group_by(TokenEvent.role, TokenEvent.complexity, TokenEvent.model)
        .having(func.count(TokenEvent.id) >= 3)
    )
    rows = (await session.execute(query)).mappings().all()
    groups: dict[tuple[str, int], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["role"], row["complexity"]), []).append(dict(row))
    result = []
    for (role, complexity), models in groups.items():
        most_used = max(models, key=lambda item: item["n"])
        cheapest = min(models, key=lambda item: item["avg_cost"])
        if most_used["model"] == cheapest["model"] or not most_used["avg_cost"]:
            continue
        savings = round((most_used["avg_cost"] - cheapest["avg_cost"]) / most_used["avg_cost"] * 100)
        if savings < 10:
            continue
        result.append(
            {
                "role": role,
                "complexity": complexity,
                "most_used_model": most_used["model"],
                "recommended_model": cheapest["model"],
                "avg_cost_most_used": most_used["avg_cost"],
                "avg_cost_recommended": cheapest["avg_cost"],
                "estimated_savings_pct": savings,
                "data_points": sum(item["n"] for item in models),
            }
        )
    return sorted(result, key=lambda item: item["estimated_savings_pct"], reverse=True)


@router.get("/by-role-model-complexity")
async def by_role_model_complexity(days: int = 30, project: Optional[str] = None, method: Optional[str] = None,
                                   session: AsyncSession = Depends(get_session)):
    conditions = [*_llm_filter(days), TokenEvent.complexity.is_not(None)]
    if project:
        conditions.append(TokenEvent.project_name == project)
    if method:
        conditions.append(TokenEvent.complexity_method == method)
    query = (
        select(
            TokenEvent.role,
            TokenEvent.model,
            TokenEvent.complexity,
            func.avg(TokenEvent.prompt_tokens + TokenEvent.completion_tokens).label("avg_actual_tokens"),
            func.count(TokenEvent.id).label("data_points"),
        )
        .where(*conditions)
        .group_by(TokenEvent.role, TokenEvent.model, TokenEvent.complexity)
        .order_by(TokenEvent.role, TokenEvent.model, TokenEvent.complexity)
    )
    rows = (await session.execute(query)).mappings().all()
    return [
        {
            "role": row["role"],
            "model": row["model"],
            "complexity": row["complexity"],
            "avg_actual_tokens": round(float(row["avg_actual_tokens"])),
            "data_points": int(row["data_points"]),
        }
        for row in rows
    ]


# ── Complexity matrix: tier x model volume, tokens, latency, errors, cost, completion ──────────
LOW_SAMPLE_TASKS = 5
_METHOD_SQL = (
    "COALESCE(e.complexity_method, json_extract(e.tags_json, '$.complexity_method'), "
    "json_extract(e.tags_json, '$.complexity_version'))"
)
_COMPLETIONS = ("session_end", "next_task", "inferred", "open", "unknown")


def _new_bucket() -> dict:
    bucket = {"calls": 0, "calls_without_task": 0, "tasks": set(), "errors": 0,
              "lat_sum": 0, "lat_n": 0, "ttft_sum": 0, "ttft_n": 0, "priced_calls": 0, "unpriced_calls": 0,
              "estimated_calls": 0, "priced_cost": 0.0, "task_priced_cost": 0.0,
              "completion": dict.fromkeys(_COMPLETIONS, 0)}
    for name in _TOKEN_FIELDS:
        bucket[name] = 0
        bucket["task_" + name] = 0
    return bucket


def _add(bucket: dict, row, key, completion: Optional[str]) -> None:
    calls = int(row["calls"])
    bucket["calls"] += calls
    for name in ("errors", "lat_sum", "lat_n", "ttft_sum", "ttft_n", "priced_calls", "unpriced_calls",
                 "estimated_calls"):
        bucket[name] += int(row[name] or 0)
    bucket["priced_cost"] += float(row["priced_cost"] or 0)
    for name in _TOKEN_FIELDS:
        bucket[name] += int(row[name] or 0)
    if key is None:
        bucket["calls_without_task"] += calls
        return
    bucket["task_priced_cost"] += float(row["priced_cost"] or 0)
    for name in _TOKEN_FIELDS:
        bucket["task_" + name] += int(row[name] or 0)
    if key not in bucket["tasks"]:
        bucket["tasks"].add(key)
        bucket["completion"][completion or "unknown"] += 1


def _avg(total, count, digits=0):
    return round(total / count, digits) if count else None


def _finish(bucket: dict) -> dict:
    calls, tasks = bucket["calls"], len(bucket["tasks"])
    priced = bucket["priced_calls"] > 0
    return {
        "calls": calls,
        "tasks": tasks,
        "calls_without_task": bucket["calls_without_task"],
        **{name: bucket[name] for name in _TOKEN_FIELDS},
        # Per call over every call; per task over the calls that belong to a task.
        "per_call": {name: _avg(bucket[name], calls) for name in _TOKEN_FIELDS},
        "per_task": {name: _avg(bucket["task_" + name], tasks) for name in _TOKEN_FIELDS},
        "avg_process_time_ms": _avg(bucket["lat_sum"], bucket["lat_n"]),
        "avg_ttft_ms": _avg(bucket["ttft_sum"], bucket["ttft_n"]),
        "error_count": bucket["errors"],
        "error_rate": round(bucket["errors"] / calls, 6) if calls else 0.0,
        "priced_calls": bucket["priced_calls"],
        "unpriced_calls": bucket["unpriced_calls"],
        "estimated_calls": bucket["estimated_calls"],
        # Priced cost only; None when nothing is priced, so unpriced usage never reads as $0.
        "priced_cost_usd": round(bucket["priced_cost"], 6) if priced else None,
        "avg_priced_cost_per_call": _avg(bucket["priced_cost"], bucket["priced_calls"], 8) if priced else None,
        "avg_priced_cost_per_task": _avg(bucket["task_priced_cost"], tasks, 8) if priced else None,
        "priced_cost_per_1k_output": (
            round(bucket["priced_cost"] / bucket["completion_tokens"] * 1000, 6)
            if priced and bucket["completion_tokens"] else None
        ),
        "cost_complete": priced and bucket["unpriced_calls"] == 0 and bucket["estimated_calls"] == 0,
        "completion": bucket["completion"],
        "low_sample": tasks < LOW_SAMPLE_TASKS,
    }


def _mark_best(cells: list[dict]) -> None:
    """Flag the lowest cost per task and lowest latency per tier among adequately sampled models."""
    tiers: dict[int, list[dict]] = {}
    for cell in cells:
        cell["lowest_cost_per_task"] = False
        cell["lowest_latency"] = False
        if not cell["low_sample"]:
            tiers.setdefault(cell["complexity"], []).append(cell)
    for group in tiers.values():
        for flag, metric, needs_full_cost in (
            ("lowest_cost_per_task", "avg_priced_cost_per_task", True),
            ("lowest_latency", "avg_process_time_ms", False),
        ):
            candidates = [c for c in group if c[metric] is not None and (c["cost_complete"] or not needs_full_cost)]
            if len(candidates) < 2:
                continue
            best = min(c[metric] for c in candidates)
            for c in candidates:
                c[flag] = c[metric] == best


@router.get("/complexity-matrix")
async def complexity_matrix(
    days: int = 30,
    project: Optional[str] = None,
    model: list[str] = Query(default=[]),
    method: str = "request-shape-v1",
    session: AsyncSession = Depends(get_session),
):
    """LLM calls grouped by call complexity tier and model.

    A task is one Hermes turn, keyed by (project, session, turn). A task whose calls span several
    tiers or models counts once in each (tier, model) cell and in each tier it has calls in.
    """
    where = "e.recorded_at >= :cutoff AND e.event_type = 'llm_request' AND e.complexity IS NOT NULL"
    params: dict = {"cutoff": _cutoff(days)}
    methods = [r[0] for r in (await session.execute(
        text(f"SELECT DISTINCT {_METHOD_SQL} AS m FROM token_events e WHERE {where} AND m IS NOT NULL ORDER BY m"),
        params,
    ))]
    params["method"] = method
    where += f" AND {_METHOD_SQL} = :method"
    projects = [r[0] for r in (await session.execute(
        text(f"SELECT DISTINCT e.project_name FROM token_events e WHERE {where} ORDER BY 1"), params))]
    if project:
        where += " AND e.project_name = :project"
        params["project"] = project
    models = [r[0] for r in (await session.execute(
        text(f"SELECT DISTINCT COALESCE(e.model, 'unknown') FROM token_events e WHERE {where} ORDER BY 1"), params))]
    if model:
        for i, name in enumerate(model):
            params[f"m{i}"] = name
        where += f" AND COALESCE(e.model, 'unknown') IN ({', '.join(f':m{i}' for i in range(len(model)))})"
    token_sums = ", ".join(f"SUM(e.{name}) AS {name}" for name in _TOKEN_FIELDS)
    query = text(
        "SELECT e.complexity AS tier, COALESCE(e.model, 'unknown') AS model, e.project_name AS project, "
        f"COALESCE(e.session_id, '') AS s, e.turn_id AS turn, COUNT(*) AS calls, {token_sums}, "
        "SUM(e.status <> 'success') AS errors, "
        "SUM(e.process_time_ms) AS lat_sum, COUNT(e.process_time_ms) AS lat_n, "
        "SUM(e.ttft_ms) AS ttft_sum, COUNT(e.ttft_ms) AS ttft_n, "
        "SUM(e.cost_status = 'priced') AS priced_calls, "
        "SUM(e.cost_status = 'unpriced') AS unpriced_calls, "
        "SUM(e.cost_status IN ('partial', 'estimated', 'legacy')) AS estimated_calls, "
        "SUM(CASE WHEN e.cost_status = 'priced' THEN e.estimated_cost_usd END) AS priced_cost, "
        "MAX(t.completion) AS completion, MAX(t.completed_at) AS completed_at, "
        "MAX(t.last_seen_at) AS last_seen_at, COUNT(t.id) AS has_task "
        "FROM token_events e LEFT JOIN tasks t ON t.project_name = e.project_name "
        "AND t.session_id = COALESCE(e.session_id, '') AND t.turn_id = e.turn_id "
        f"WHERE {where} GROUP BY tier, model, project, s, turn"
    )
    now_dt = datetime.now(timezone.utc)
    cells: dict[tuple[int, str], dict] = {}
    tiers: dict[int, dict] = {}
    for row in (await session.execute(query, params)).mappings():
        key = (row["project"], row["s"], row["turn"]) if row["turn"] is not None else None
        completion = _completion(dict(row), now_dt)[0] if row["has_task"] else None
        for bucket in (cells.setdefault((row["tier"], row["model"]), _new_bucket()),
                       tiers.setdefault(row["tier"], _new_bucket())):
            _add(bucket, row, key, completion)
    cell_items = [{"complexity": tier, "model": name, **_finish(bucket)} for (tier, name), bucket in sorted(cells.items())]
    _mark_best(cell_items)
    return {
        "method": method,
        "tier_source": "llm_call",
        "low_sample_tasks": LOW_SAMPLE_TASKS,
        "methods": methods,
        "projects": projects,
        "models": models,
        "tiers": [{"complexity": tier, **_finish(bucket)} for tier, bucket in sorted(tiers.items())],
        "cells": cell_items,
    }
