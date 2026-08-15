from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_session
from models import TokenEvent

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
        }
        result.append(item)
    return result


@router.get("/by-project")
async def by_project(days: int = 30, session: AsyncSession = Depends(get_session)):
    return await _grouped(session, field=TokenEvent.project_name, label="project_name", days=days)


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
        }
        for row in rows
    ]


@router.get("/by-complexity")
async def by_complexity(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    conditions = [*_llm_filter(days), TokenEvent.complexity.is_not(None)]
    if project:
        conditions.append(TokenEvent.project_name == project)
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
async def routing_recommendations(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    conditions = [
        *_llm_filter(days),
        TokenEvent.complexity.is_not(None),
        TokenEvent.role.is_not(None),
        TokenEvent.cost_status == "priced",
    ]
    if project:
        conditions.append(TokenEvent.project_name == project)
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
async def by_role_model_complexity(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    conditions = [*_llm_filter(days), TokenEvent.complexity.is_not(None)]
    if project:
        conditions.append(TokenEvent.project_name == project)
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
