from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from models import ModelAlias, PricingRule

_DATE_SUFFIX = re.compile(r"-\d{8}$")


@dataclass(frozen=True)
class CostResult:
    cost: Optional[float]
    status: str
    reason: Optional[str]
    pricing_model: Optional[str]
    pricing_version: Optional[int]


async def resolve_pricing_rule(
    session: AsyncSession,
    model: str,
    model_requested: Optional[str] = None,
) -> tuple[Optional[str], Optional[PricingRule]]:
    candidates: list[str] = []

    async def add_candidate(value: Optional[str]) -> None:
        if not value or value in candidates:
            return
        candidates.append(value)
        alias = await session.get(ModelAlias, value)
        if alias and alias.model not in candidates:
            candidates.append(alias.model)
        stripped = _DATE_SUFFIX.sub("", value)
        if stripped != value and stripped not in candidates:
            candidates.append(stripped)

    await add_candidate(model)
    await add_candidate(model_requested)

    for candidate in candidates:
        rule = await session.get(PricingRule, candidate)
        if rule is not None:
            return candidate, rule
    return None, None


def calculate_cost(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    total_tokens: Optional[int],
    pricing_model: Optional[str],
    rule: Optional[PricingRule],
    normalization_reason: Optional[str] = None,
) -> CostResult:
    if rule is None:
        return CostResult(None, "unpriced", "no_rule", pricing_model, None)

    if (
        total_tokens is not None
        and prompt_tokens == 0
        and completion_tokens == 0
        and cache_read_tokens == 0
        and cache_creation_tokens == 0
    ):
        average_rate = (rule.input_price_per_1m + rule.output_price_per_1m) / 2
        return CostResult(
            total_tokens / 1_000_000 * average_rate,
            "estimated",
            normalization_reason or "total_tokens_only",
            pricing_model,
            rule.pricing_version,
        )

    cost = (
        prompt_tokens / 1_000_000 * rule.input_price_per_1m
        + completion_tokens / 1_000_000 * rule.output_price_per_1m
    )
    missing_cache_price = False

    if cache_read_tokens:
        if rule.cache_read_price_per_1m is None:
            missing_cache_price = True
        else:
            cost += cache_read_tokens / 1_000_000 * rule.cache_read_price_per_1m

    if cache_creation_tokens:
        if rule.cache_creation_price_per_1m is None:
            missing_cache_price = True
        else:
            cost += cache_creation_tokens / 1_000_000 * rule.cache_creation_price_per_1m

    if missing_cache_price:
        return CostResult(
            cost,
            "partial",
            normalization_reason or "cache_price_missing",
            pricing_model,
            rule.pricing_version,
        )
    return CostResult(
        cost,
        "priced",
        normalization_reason,
        pricing_model,
        rule.pricing_version,
    )
