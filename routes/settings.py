from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_session
from models import ModelAlias, PricingRule, TokenEvent, _now
from pricing import calculate_cost, resolve_pricing_rule

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PricingRuleIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model: str = Field(min_length=1, max_length=128)
    input_price_per_1m: float = Field(ge=0)
    output_price_per_1m: float = Field(ge=0)
    cache_read_price_per_1m: Optional[float] = Field(default=None, ge=0)
    cache_creation_price_per_1m: Optional[float] = Field(default=None, ge=0)


class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)


@router.get("/pricing")
async def list_pricing(session: AsyncSession = Depends(get_session)):
    rows = (await session.exec(select(PricingRule).order_by(PricingRule.model))).all()
    return [row.model_dump() for row in rows]


@router.post("/pricing")
async def upsert_pricing(body: PricingRuleIn, session: AsyncSession = Depends(get_session)):
    existing = await session.get(PricingRule, body.model)
    if existing:
        existing.input_price_per_1m = body.input_price_per_1m
        existing.output_price_per_1m = body.output_price_per_1m
        existing.cache_read_price_per_1m = body.cache_read_price_per_1m
        existing.cache_creation_price_per_1m = body.cache_creation_price_per_1m
        existing.pricing_version += 1
        existing.updated_at = _now()
        rule = existing
    else:
        rule = PricingRule(**body.model_dump(), pricing_version=1, updated_at=_now())
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule.model_dump()


@router.delete("/pricing/{model:path}", status_code=204)
async def delete_pricing(model: str, session: AsyncSession = Depends(get_session)):
    rule = await session.get(PricingRule, model)
    if not rule:
        raise HTTPException(status_code=404, detail="Pricing rule not found")
    await session.delete(rule)
    await session.commit()


@router.get("/aliases")
async def list_aliases(session: AsyncSession = Depends(get_session)):
    rows = (await session.exec(select(ModelAlias).order_by(ModelAlias.alias))).all()
    return [row.model_dump() for row in rows]


@router.post("/aliases")
async def upsert_alias(body: AliasIn, session: AsyncSession = Depends(get_session)):
    if body.alias == body.model:
        raise HTTPException(status_code=422, detail="Alias cannot point to itself")
    existing = await session.get(ModelAlias, body.alias)
    if existing:
        existing.model = body.model
        existing.updated_at = _now()
        alias = existing
    else:
        alias = ModelAlias(alias=body.alias, model=body.model, updated_at=_now())
    session.add(alias)
    await session.commit()
    return alias.model_dump()


@router.delete("/aliases/{alias:path}", status_code=204)
async def delete_alias(alias: str, session: AsyncSession = Depends(get_session)):
    row = await session.get(ModelAlias, alias)
    if row is None:
        raise HTTPException(status_code=404, detail="Alias not found")
    await session.delete(row)
    await session.commit()


@router.get("/unpriced-models")
async def unpriced_models(session: AsyncSession = Depends(get_session)):
    query = (
        select(
            TokenEvent.model,
            func.count(TokenEvent.id).label("event_count"),
            func.min(TokenEvent.recorded_at).label("first_seen"),
            func.max(TokenEvent.recorded_at).label("last_seen"),
        )
        .where(TokenEvent.event_type == "llm_request", TokenEvent.cost_status == "unpriced")
        .group_by(TokenEvent.model)
        .order_by(func.count(TokenEvent.id).desc())
    )
    rows = (await session.exec(query)).all()
    return [
        {
            "model": row[0],
            "event_count": row[1],
            "first_seen": row[2],
            "last_seen": row[3],
        }
        for row in rows
    ]


@router.post("/recost")
async def recost(
    dry_run: bool = True,
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.exec(
            select(TokenEvent).where(
                TokenEvent.event_type == "llm_request",
                TokenEvent.cost_status.in_(["unpriced", "partial", "estimated", "legacy"]),
            )
        )
    ).all()
    changed = 0
    deltas: dict[str, dict[str, float | int]] = {}
    for event in rows:
        pricing_model, rule = await resolve_pricing_rule(session, event.model, event.model_requested)
        result = calculate_cost(
            prompt_tokens=event.prompt_tokens,
            completion_tokens=event.completion_tokens,
            cache_read_tokens=event.cache_read_tokens,
            cache_creation_tokens=event.cache_creation_tokens,
            total_tokens=event.total_tokens,
            pricing_model=pricing_model,
            rule=rule,
        )
        old_cost = event.estimated_cost_usd or 0.0
        new_cost = result.cost or 0.0
        if (
            event.estimated_cost_usd != result.cost
            or event.cost_status != result.status
            or event.pricing_version != result.pricing_version
        ):
            changed += 1
            bucket = deltas.setdefault(event.model, {"events": 0, "cost_delta_usd": 0.0})
            bucket["events"] = int(bucket["events"]) + 1
            bucket["cost_delta_usd"] = float(bucket["cost_delta_usd"]) + new_cost - old_cost
            if not dry_run:
                event.estimated_cost_usd = result.cost
                event.cost_status = result.status
                event.cost_status_reason = result.reason
                event.pricing_model = result.pricing_model
                event.pricing_version = result.pricing_version
                session.add(event)
    if not dry_run:
        if changed:
            # Stored costs changed under already-snapshotted rows: expire open export cursors (O9 X1).
            await session.execute(text("UPDATE export_state SET revision = revision + 1 WHERE id = 1"))
        await session.commit()
    return {
        "dry_run": dry_run,
        "scanned": len(rows),
        "changed": changed,
        "models": {
            model: {"events": data["events"], "cost_delta_usd": round(float(data["cost_delta_usd"]), 9)}
            for model, data in sorted(deltas.items())
        },
    }
