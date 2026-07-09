from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from collections import defaultdict

from database import get_session
from models import TokenEvent

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _cutoff(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


async def _fetch(session: AsyncSession, days: int, project: Optional[str] = None):
    q = select(TokenEvent).where(TokenEvent.recorded_at >= _cutoff(days))
    if project:
        q = q.where(TokenEvent.project_name == project)
    result = await session.exec(q)
    return result.all()


@router.get("/summary")
async def summary(days: int = 7, session: AsyncSession = Depends(get_session)):
    rows = await _fetch(session, days)
    total_events = len(rows)
    total_prompt = sum(r.prompt_tokens for r in rows)
    total_completion = sum(r.completion_tokens for r in rows)
    total_cost = sum(r.estimated_cost_usd for r in rows if r.estimated_cost_usd is not None)
    unique_projects = len({r.project_name for r in rows})
    unique_models = len({r.model for r in rows})
    latencies = [r.process_time_ms for r in rows if r.process_time_ms is not None]
    avg_latency = sum(latencies) / len(latencies) if latencies else None
    return {
        "total_events": total_events,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_cost_usd": round(total_cost, 6),
        "unique_projects": unique_projects,
        "unique_models": unique_models,
        "avg_process_time_ms": round(avg_latency) if avg_latency is not None else None,
    }


@router.get("/by-project")
async def by_project(days: int = 30, session: AsyncSession = Depends(get_session)):
    rows = await _fetch(session, days)
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r.project_name].append(r)

    result = []
    for proj, items in groups.items():
        total_tokens = sum(r.prompt_tokens + r.completion_tokens for r in items)
        total_cost = sum(r.estimated_cost_usd for r in items if r.estimated_cost_usd is not None)
        latencies = [r.process_time_ms for r in items if r.process_time_ms is not None]
        avg_latency = sum(latencies) / len(latencies) if latencies else None
        result.append({
            "project_name": proj,
            "event_count": len(items),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "avg_process_time_ms": round(avg_latency) if avg_latency is not None else None,
        })
    return sorted(result, key=lambda x: x["total_cost_usd"], reverse=True)


@router.get("/by-model")
async def by_model(days: int = 30, session: AsyncSession = Depends(get_session)):
    rows = await _fetch(session, days)
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r.model].append(r)

    result = []
    for model, items in groups.items():
        avg_prompt = sum(r.prompt_tokens for r in items) / len(items)
        avg_completion = sum(r.completion_tokens for r in items) / len(items)
        total_cost = sum(r.estimated_cost_usd for r in items if r.estimated_cost_usd is not None)
        total_tokens = sum(r.prompt_tokens + r.completion_tokens for r in items)
        latencies = [r.process_time_ms for r in items if r.process_time_ms is not None]
        avg_latency = sum(latencies) / len(latencies) if latencies else None
        cost_per_1k = (total_cost / total_tokens * 1000) if total_tokens > 0 else None
        result.append({
            "model": model,
            "event_count": len(items),
            "avg_prompt_tokens": round(avg_prompt),
            "avg_completion_tokens": round(avg_completion),
            "avg_process_time_ms": round(avg_latency) if avg_latency is not None else None,
            "total_cost_usd": round(total_cost, 6),
            "cost_per_1k_tokens": round(cost_per_1k, 6) if cost_per_1k is not None else None,
        })
    return sorted(result, key=lambda x: x["total_cost_usd"], reverse=True)


@router.get("/by-role")
async def by_role(days: int = 30, session: AsyncSession = Depends(get_session)):
    rows = await _fetch(session, days)
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r.role or "unknown"].append(r)

    result = []
    for role, items in groups.items():
        total_tokens = sum(r.prompt_tokens + r.completion_tokens for r in items)
        total_cost = sum(r.estimated_cost_usd for r in items if r.estimated_cost_usd is not None)
        result.append({
            "role": role,
            "event_count": len(items),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
        })
    return sorted(result, key=lambda x: x["total_cost_usd"], reverse=True)


@router.get("/timeseries")
async def timeseries(
    days: int = 30,
    granularity: str = "day",
    project: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    rows = await _fetch(session, days, project)
    buckets: dict[str, dict] = defaultdict(lambda: {"total_tokens": 0, "total_cost_usd": 0.0, "event_count": 0})

    for r in rows:
        date_str = r.recorded_at[:10]  # YYYY-MM-DD
        buckets[date_str]["total_tokens"] += r.prompt_tokens + r.completion_tokens
        buckets[date_str]["total_cost_usd"] += r.estimated_cost_usd or 0.0
        buckets[date_str]["event_count"] += 1

    result = [
        {"date": date, **{k: round(v, 6) if isinstance(v, float) else v for k, v in data.items()}}
        for date, data in sorted(buckets.items())
    ]
    return result


@router.get("/by-complexity")
async def by_complexity(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    rows = await _fetch(session, days, project)
    groups: dict[int, list] = defaultdict(list)
    for r in rows:
        if r.complexity is not None:
            groups[r.complexity].append(r)

    result = []
    for complexity, items in groups.items():
        costs = [r.estimated_cost_usd for r in items if r.estimated_cost_usd is not None]
        avg_cost = round(sum(costs) / len(costs), 6) if costs else None
        avg_tokens = round(sum(r.prompt_tokens + r.completion_tokens for r in items) / len(items))
        result.append({
            "complexity": complexity,
            "avg_cost_usd": avg_cost,
            "avg_tokens": avg_tokens,
            "event_count": len(items),
        })
    return sorted(result, key=lambda x: x["complexity"])


@router.get("/routing-recommendations")
async def routing_recommendations(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    rows = await _fetch(session, days, project)
    bucket_events: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r.complexity is not None and r.estimated_cost_usd is not None and r.role is not None:
            bucket_events[(r.role, r.complexity, r.model)].append(r)

    qualifying: dict[tuple, dict] = {}
    for (role, complexity, model), items in bucket_events.items():
        if len(items) >= 3:
            avg_cost = sum(r.estimated_cost_usd for r in items) / len(items)
            qualifying[(role, complexity, model)] = {"count": len(items), "avg_cost": avg_cost}

    role_complexity_groups: dict[tuple, dict] = defaultdict(dict)
    for (role, complexity, model), data in qualifying.items():
        role_complexity_groups[(role, complexity)][model] = data

    result = []
    for (role, complexity), model_data in role_complexity_groups.items():
        most_used_model = max(model_data, key=lambda m: model_data[m]["count"])
        cheapest_model = min(model_data, key=lambda m: model_data[m]["avg_cost"])

        if cheapest_model == most_used_model:
            continue

        avg_cost_most_used = model_data[most_used_model]["avg_cost"]
        avg_cost_cheapest = model_data[cheapest_model]["avg_cost"]

        if avg_cost_most_used == 0:
            continue

        savings_pct = round((avg_cost_most_used - avg_cost_cheapest) / avg_cost_most_used * 100)
        if savings_pct < 10:
            continue

        data_points = sum(d["count"] for d in model_data.values())
        result.append({
            "role": role,
            "complexity": complexity,
            "most_used_model": most_used_model,
            "recommended_model": cheapest_model,
            "avg_cost_most_used": avg_cost_most_used,
            "avg_cost_recommended": avg_cost_cheapest,
            "estimated_savings_pct": savings_pct,
            "data_points": data_points,
        })
    return sorted(result, key=lambda x: x["estimated_savings_pct"], reverse=True)


@router.get("/by-role-model-complexity")
async def by_role_model_complexity(days: int = 30, project: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    rows = await _fetch(session, days, project)
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r.complexity is not None:
            groups[(r.role, r.model, r.complexity)].append(r)

    result = []
    for (role, model, complexity), items in groups.items():
        avg_actual = round(sum(r.prompt_tokens + r.completion_tokens for r in items) / len(items))
        result.append({
            "role": role,
            "model": model,
            "complexity": complexity,
            "avg_actual_tokens": avg_actual,
            "data_points": len(items),
        })
    return sorted(result, key=lambda x: (x["role"] or "", x["model"], x["complexity"]))
