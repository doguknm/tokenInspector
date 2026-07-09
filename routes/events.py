import uuid
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_session
from models import TokenEvent, PricingRule, _now
from complexity_scorer import score_complexity

router = APIRouter(prefix="/api/events", tags=["events"])


class EventIn(BaseModel):
    model: str
    role: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: Optional[int] = None
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    process_time_ms: Optional[int] = None
    request_size_bytes: Optional[int] = None
    response_size_bytes: Optional[int] = None
    status: str = "success"
    error_message: Optional[str] = None
    complexity: Optional[int] = None
    prompt_text: Optional[str] = None


def _calc_cost(
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: Optional[int],
    rule: Optional[PricingRule],
) -> Optional[float]:
    if rule is None:
        return None
    if total_tokens is not None and prompt_tokens == 0 and completion_tokens == 0:
        avg_rate = (rule.input_price_per_1m + rule.output_price_per_1m) / 2
        return total_tokens / 1_000_000 * avg_rate
    return (prompt_tokens / 1_000_000 * rule.input_price_per_1m +
            completion_tokens / 1_000_000 * rule.output_price_per_1m)


@router.post("", status_code=201)
async def create_event(
    body: EventIn,
    background_tasks: BackgroundTasks,
    x_project_name: str = Header(..., alias="X-Project-Name"),
    session: AsyncSession = Depends(get_session),
):
    rule = await session.get(PricingRule, body.model)
    cost = _calc_cost(body.prompt_tokens, body.completion_tokens, body.total_tokens, rule)
    pt = (body.prompt_text or "")[:3000] or None

    event = TokenEvent(
        id=str(uuid.uuid4()),
        project_name=x_project_name,
        model=body.model,
        role=body.role,
        prompt_tokens=body.prompt_tokens,
        completion_tokens=body.completion_tokens,
        cache_read_tokens=body.cache_read_tokens,
        cache_creation_tokens=body.cache_creation_tokens,
        process_time_ms=body.process_time_ms,
        request_size_bytes=body.request_size_bytes,
        response_size_bytes=body.response_size_bytes,
        estimated_cost_usd=cost,
        status=body.status,
        error_message=body.error_message,
        complexity=body.complexity,
        prompt_text=pt,
        recorded_at=_now(),
    )
    session.add(event)
    await session.commit()
    if body.complexity is None and pt is not None:
        background_tasks.add_task(score_complexity, event.id, pt)
    return {"id": event.id, "estimated_cost_usd": cost}


@router.get("")
async def list_events(
    project: Optional[str] = None,
    model: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    session: AsyncSession = Depends(get_session),
):
    query = select(TokenEvent).order_by(TokenEvent.recorded_at.desc())
    if project:
        query = query.where(TokenEvent.project_name == project)
    if model:
        query = query.where(TokenEvent.model == model)
    if status:
        query = query.where(TokenEvent.status == status)

    all_rows = (await session.exec(query)).all()
    total = len(all_rows)
    offset = (page - 1) * page_size
    items = all_rows[offset: offset + page_size]
    return {"items": [r.model_dump() for r in items], "total": total, "page": page, "page_size": page_size}
