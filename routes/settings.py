from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_session
from models import PricingRule, _now

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PricingRuleIn(BaseModel):
    model: str
    input_price_per_1m: float
    output_price_per_1m: float


@router.get("/pricing")
async def list_pricing(session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(PricingRule).order_by(PricingRule.model))
    return [
        {
            "model": r.model,
            "input_price_per_1m": r.input_price_per_1m,
            "output_price_per_1m": r.output_price_per_1m,
            "updated_at": r.updated_at,
        }
        for r in result.all()
    ]


@router.post("/pricing")
async def upsert_pricing(body: PricingRuleIn, session: AsyncSession = Depends(get_session)):
    existing = await session.get(PricingRule, body.model)
    if existing:
        existing.input_price_per_1m = body.input_price_per_1m
        existing.output_price_per_1m = body.output_price_per_1m
        existing.updated_at = _now()
        session.add(existing)
    else:
        session.add(PricingRule(
            model=body.model,
            input_price_per_1m=body.input_price_per_1m,
            output_price_per_1m=body.output_price_per_1m,
            updated_at=_now(),
        ))
    await session.commit()
    return {"model": body.model, "input_price_per_1m": body.input_price_per_1m, "output_price_per_1m": body.output_price_per_1m}


@router.delete("/pricing/{model:path}", status_code=204)
async def delete_pricing(model: str, session: AsyncSession = Depends(get_session)):
    rule = await session.get(PricingRule, model)
    if not rule:
        raise HTTPException(status_code=404, detail="Pricing rule not found")
    await session.delete(rule)
    await session.commit()
