"""JEV difficulty worker (backend.md §6). Runs only via POST /api/tasks/evaluate.

Every HTTP call is preceded by a committed `evaluator_attempts` reservation (the durable budget
ledger) and a persisted per-provider cooldown check. Retry-After is honoured exactly; typesafe-ai
falls back to digitalocean only after a 429. The AI Gateway key is read from the environment and
is never logged, returned or stored. Evaluator usage is recorded as a `token-inspector` event with
no task_id, so it is never scored itself.
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import text

import features
from database import AsyncSessionLocal

log = logging.getLogger(__name__)

MODEL = "typesafe-ai/jev"
ENDPOINT = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
PROVIDERS = ("typesafe-ai", "digitalocean")
RUBRIC_VERSION = "difficulty-v0"
PRICE_PER_TOKEN = 0.042 / 1_000_000
RESERVE_OVERHEAD_TOKENS = 1024
MAX_ATTEMPTS_PER_PROVIDER = 2
TASK_SPACING_S = 0.5
EVALUATOR_PROJECT = "token-inspector"
INSTRUCTIONS = (
    "Rate the start-of-task difficulty of this software-engineering request, judged only from the request text."
)
CRITERIA = [
    "Single obvious routine step",
    "A few known steps",
    "Multi-file / multi-step analysis with tests",
    "Unclear root cause or multi-system coordination",
    "Open-ended research or deep architectural uncertainty",
]
BACKOFF_S = (2.0, 4.0)

# Injection points for tests.
sleep = asyncio.sleep


def now() -> datetime:
    return datetime.now(timezone.utc)


async def post_evaluation(body: dict, api_key: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(
            ENDPOINT, json=body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def questions() -> dict:
    return {"difficulty": {"type": "score", "instructions": INSTRUCTIONS, "criteria": list(CRITERIA)}}


def input_hash(state: str) -> str:
    payload = json.dumps({"rubric_version": RUBRIC_VERSION, "state": state, "questions": questions()}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def request_body(state: str, provider: str) -> dict:
    return {"model": MODEL, "state": state, "questions": questions(),
            "providerOptions": {"gateway": {"only": [provider]}}}


def parse_retry_after(value: Optional[str], at: datetime) -> Optional[float]:
    """delta-seconds or an HTTP-date; None when absent or malformed."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(int(value))
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - at).total_seconds())


class InvalidResponse(ValueError):
    pass


def _number(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidResponse("not a finite number")
    if not low <= value <= high:
        raise InvalidResponse("out of range")
    return float(value)


def _five(value: Any) -> list:
    if isinstance(value, list):
        if len(value) != 5:
            raise InvalidResponse("expected 5 entries")
        return value
    if isinstance(value, dict):
        if set(value) != {"0", "1", "2", "3", "4"}:
            raise InvalidResponse("expected keys 0..4")
        return [value[str(i)] for i in range(5)]
    raise InvalidResponse("expected 5 levels")


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def validate(body: Any) -> dict:
    """Strict validation; any failure means invalid_response and no numeric value is stored."""
    if not isinstance(body, dict):
        raise InvalidResponse("body is not an object")
    try:
        answer = body["answers"]["difficulty"]
    except (KeyError, TypeError) as exc:
        raise InvalidResponse("missing answers.difficulty") from exc
    if not isinstance(answer, dict) or answer.get("type") != "score":
        raise InvalidResponse("answer type is not score")
    score = _number(answer.get("score"), 0.0, 4.0)
    confidence = _number(answer.get("confidence"), 0.0, 1.0)
    probabilities = [_number(p, 0.0, 1.0) for p in _five(answer.get("probabilities"))]
    if abs(sum(probabilities) - 1.0) > 0.02:
        raise InvalidResponse("probabilities do not sum to 1")
    legend = _five(answer.get("legend"))
    if legend != CRITERIA:
        raise InvalidResponse("legend does not match the rubric criteria")
    for key in ("usage", "provider_metadata"):
        if key in body and not isinstance(body[key], dict):
            raise InvalidResponse(f"{key} is not an object")
    usage = _mapping(body.get("usage"))
    tokens = usage.get("input_tokens")
    if tokens is not None and (isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0):
        raise InvalidResponse("invalid usage.input_tokens")
    gateway = _mapping(_mapping(body.get("provider_metadata")).get("gateway"))
    if "gateway" in _mapping(body.get("provider_metadata")) and not isinstance(
            body["provider_metadata"]["gateway"], dict):
        raise InvalidResponse("gateway metadata is not an object")
    routing = gateway.get("routing")
    if routing is not None and not isinstance(routing, dict):
        raise InvalidResponse("routing is not an object")
    cost = gateway.get("cost")
    if cost is not None:
        try:
            cost = float(cost)
        except (TypeError, ValueError) as exc:
            raise InvalidResponse("invalid gateway cost") from exc
        if not math.isfinite(cost) or cost < 0:
            raise InvalidResponse("invalid gateway cost")
    return {
        "raw_score": score,
        "confidence": confidence,
        "probabilities": probabilities,
        "legend": legend,
        "input_tokens": tokens,
        "cost_usd": cost,
        "provider_used": _mapping(routing).get("finalProvider"),
    }


def usage_of(body: Any) -> tuple[Optional[int], Optional[float]]:
    """Best-effort usage/cost for reconciliation when the answer itself is invalid."""
    try:
        tokens = _mapping(_mapping(body).get("usage")).get("input_tokens")
        tokens = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0 else None
        cost = _mapping(_mapping(_mapping(body).get("provider_metadata")).get("gateway")).get("cost")
        cost = float(cost) if cost is not None else None
        if cost is not None and (not math.isfinite(cost) or cost < 0):
            cost = None
    except Exception:
        return None, None
    return tokens, cost


# ---- ledger, budget and cooldowns -------------------------------------------------------


async def spend(session, at: datetime) -> dict:
    day, month = _fmt(at.replace(hour=0, minute=0, second=0, microsecond=0)), _fmt(
        at.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    row = (
        await session.execute(
            text(
                "SELECT "
                "COALESCE(SUM(CASE WHEN started_at >= :day THEN COALESCE(actual_cost_usd, reserved_cost_usd) END), 0), "
                "COALESCE(SUM(CASE WHEN started_at >= :month THEN COALESCE(actual_cost_usd, reserved_cost_usd) END), 0), "
                "COALESCE(SUM(started_at >= :day), 0) FROM evaluator_attempts WHERE started_at >= :month"
            ),
            {"day": day, "month": month},
        )
    ).one()
    return {"day_usd": float(row[0]), "month_usd": float(row[1]), "calls_day": int(row[2])}


async def cooldowns(session) -> dict[str, datetime]:
    rows = (await session.execute(text("SELECT provider, not_before FROM provider_cooldowns"))).all()
    return {p: _parse(nb) for p, nb in rows}


async def set_cooldown(session, provider: str, until: datetime, reason: str) -> None:
    await session.execute(
        text(
            "INSERT INTO provider_cooldowns (provider, not_before, reason, updated_at) VALUES (:p, :nb, :r, :now) "
            "ON CONFLICT(provider) DO UPDATE SET not_before = MAX(provider_cooldowns.not_before, excluded.not_before), "
            "reason = excluded.reason, updated_at = excluded.updated_at"
        ),
        {"p": provider, "nb": _fmt(until), "r": reason, "now": _fmt(now())},
    )
    await session.commit()


def blocked_until(cool: dict[str, datetime], at: datetime, max_wait: float) -> Optional[datetime]:
    """When every provider is cooling beyond the allowance: the earliest time one frees up."""
    waits = {p: cool.get(p) for p in PROVIDERS}
    if all(nb is not None and (nb - at).total_seconds() > max_wait for nb in waits.values()):
        return min(waits.values())
    return None


# ---- one task --------------------------------------------------------------------------


@dataclass
class TaskOutcome:
    status: str  # ok | error | rate_limited | deferred | skipped
    stop_reason: Optional[str] = None
    retry_not_before: Optional[datetime] = None
    error_type: Optional[str] = None
    result: Optional[dict] = None
    attempts: list = field(default_factory=list)


async def _reserve(session, run_id: str, ref: str, provider: str, body: dict) -> dict:
    reserved_tokens = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + RESERVE_OVERHEAD_TOKENS
    attempt = {"id": str(uuid.uuid4()), "provider": provider, "reserved_tokens": reserved_tokens,
               "reserved_cost_usd": reserved_tokens * PRICE_PER_TOKEN, "started": time.monotonic()}
    await session.execute(
        text(
            "INSERT INTO evaluator_attempts (id, run_id, task_ref, provider, status, reserved_tokens, "
            "reserved_cost_usd, started_at) VALUES (:id, :run, :ref, :p, 'reserved', :tok, :cost, :at)"
        ),
        {"id": attempt["id"], "run": run_id, "ref": ref, "p": provider, "tok": reserved_tokens,
         "cost": attempt["reserved_cost_usd"], "at": _fmt(now())},
    )
    await session.commit()  # the reservation is durable before the HTTP call
    return attempt


async def _complete(session, attempt: dict, *, status: str, http_status: Optional[int] = None,
                    error_type: Optional[str] = None, tokens: Optional[int] = None, cost: Optional[float] = None,
                    retry_after: Optional[float] = None) -> None:
    if tokens is not None and cost is None:
        cost = tokens * PRICE_PER_TOKEN
    if cost is not None and cost > attempt["reserved_cost_usd"]:
        log.warning("[JEV] reservation exceeded")
    attempt.update({"tokens": tokens, "cost": cost, "status": status,
                    "latency_ms": int((time.monotonic() - attempt["started"]) * 1000)})
    await session.execute(
        text(
            "UPDATE evaluator_attempts SET status = :s, http_status = :h, error_type = :e, actual_input_tokens = :t, "
            "actual_cost_usd = :c, retry_after_s = :ra, completed_at = :at WHERE id = :id"
        ),
        {"s": status, "h": http_status, "e": error_type, "t": tokens, "c": cost, "ra": retry_after,
         "at": _fmt(now()), "id": attempt["id"]},
    )
    await session.commit()


async def evaluate_task(session, run_id: str, ref: str, config: features.Budget, api_key: str) -> TaskOutcome:
    row = (
        await session.execute(
            text("SELECT project_name, prompt_text, prompt_expires_at, prompt_purged_at FROM tasks WHERE id = :id"),
            {"id": ref},
        )
    ).first()
    if row is None:
        return TaskOutcome("skipped", error_type="not_found")
    if row[0] == EVALUATOR_PROJECT:
        return TaskOutcome("skipped", error_type="evaluator_task")
    if row[3] is not None:
        return TaskOutcome("skipped", error_type="prompt_purged")
    if row[1] is None:
        return TaskOutcome("skipped", error_type="no_prompt")
    if row[2] is None or row[2] <= _fmt(now()):
        return TaskOutcome("skipped", error_type="prompt_expired")  # expired text is never sent
    state = row[1]
    digest = input_hash(state)
    if (await session.execute(
            text("SELECT 1 FROM task_evaluations WHERE task_ref = :t AND evaluator = 'jev' AND status = 'ok' "
                 "AND rubric_version = :r AND input_hash = :h"), {"t": ref, "r": RUBRIC_VERSION, "h": digest})).first():
        return TaskOutcome("skipped", error_type="already_scored")

    outcome = TaskOutcome("error")
    used = {p: 0 for p in PROVIDERS}
    provider = PROVIDERS[0]
    last_typesafe_429 = False
    while True:
        cool = await cooldowns(session)
        at = now()
        not_before = cool.get(provider)
        wait = (not_before - at).total_seconds() if not_before else 0.0
        if wait > config.max_retry_wait_s:
            if provider == PROVIDERS[0]:
                provider, last_typesafe_429 = PROVIDERS[1], True
                continue
            outcome.status, outcome.stop_reason = "deferred", "deferred_rate_limited"
            outcome.retry_not_before = min(nb for nb in (cool.get(p) for p in PROVIDERS) if nb)
            return outcome
        if wait > 0:
            await sleep(wait)
        if used[provider] >= MAX_ATTEMPTS_PER_PROVIDER:
            if provider == PROVIDERS[0] and last_typesafe_429:
                provider = PROVIDERS[1]
                continue
            return outcome
        fresh = (
            await session.execute(
                text("SELECT prompt_text, prompt_expires_at, prompt_purged_at FROM tasks WHERE id = :id"), {"id": ref}
            )
        ).first()
        if fresh is None or fresh[2] is not None or fresh[0] is None or fresh[1] is None or fresh[1] <= _fmt(now()):
            outcome.error_type = "prompt_expired" if fresh is not None and fresh[2] is None else "prompt_purged"
            outcome.status = "skipped" if not outcome.attempts else "error"
            return outcome  # retention ended during a wait: the text is never sent
        state = fresh[0]
        body = request_body(state, provider)
        totals = await spend(session, now())
        reserved_cost = (len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + RESERVE_OVERHEAD_TOKENS) \
            * PRICE_PER_TOKEN
        if (totals["day_usd"] + reserved_cost > config.day_usd
                or totals["month_usd"] + reserved_cost > config.month_usd
                or totals["calls_day"] + 1 > config.max_calls_per_day):
            log.warning("[JEV] budget ceiling reached; run stopped")
            outcome.stop_reason = "budget_exceeded"
            outcome.status = "skipped" if not outcome.attempts else outcome.status
            outcome.error_type = "budget_exceeded"
            return outcome
        attempt = await _reserve(session, run_id, ref, provider, body)
        outcome.attempts.append(attempt)
        used[provider] += 1
        try:
            response = await post_evaluation(body, api_key)
        except (httpx.TimeoutException, asyncio.TimeoutError):
            await _complete(session, attempt, status="failed", error_type="timeout")
            outcome.error_type = "timeout"
        except Exception:
            await _complete(session, attempt, status="failed", error_type="connection_error")
            outcome.error_type = "connection_error"
        else:
            code = response.status_code
            if 200 <= code < 300:
                payload = None
                try:
                    payload = response.json()
                    result = validate(payload)
                except Exception:  # any malformed body is an invalid_response, never a crashed run
                    tokens, cost = usage_of(payload) if isinstance(payload, dict) else (None, None)
                    await _complete(session, attempt, status="succeeded", http_status=code,
                                    error_type="invalid_response", tokens=tokens, cost=cost)
                    outcome.status, outcome.error_type = "error", "invalid_response"
                    return outcome
                await _complete(session, attempt, status="succeeded", http_status=code,
                                tokens=result["input_tokens"], cost=result["cost_usd"])
                if result["provider_used"] and result["provider_used"] != provider:
                    log.warning("[JEV] gateway routed to %s instead of %s", result["provider_used"], provider)
                result["provider_used"] = result["provider_used"] or provider
                outcome.status, outcome.result, outcome.error_type = "ok", result, None
                return outcome
            if code == 429:
                parsed = parse_retry_after(response.headers.get("retry-after"), now())
                delay = parsed if parsed is not None else BACKOFF_S[min(used[provider], 2) - 1]
                await _complete(session, attempt, status="failed", http_status=429, error_type="rate_limited",
                                retry_after=parsed)
                await set_cooldown(session, provider, now() + timedelta(seconds=delay),
                                   "retry_after" if parsed is not None else "backoff")
                outcome.error_type = "rate_limited"
                if provider == PROVIDERS[0]:
                    last_typesafe_429 = True
                    if delay > config.max_retry_wait_s or used[provider] >= MAX_ATTEMPTS_PER_PROVIDER:
                        provider = PROVIDERS[1]  # never retries typesafe-ai early
                    continue
                if delay > config.max_retry_wait_s:
                    outcome.status, outcome.stop_reason = "deferred", "deferred_rate_limited"
                    outcome.retry_not_before = now() + timedelta(seconds=delay)
                    return outcome
                if used[provider] >= MAX_ATTEMPTS_PER_PROVIDER:
                    outcome.status = "rate_limited"
                    return outcome
                continue
            if code in (401, 403):
                await _complete(session, attempt, status="failed", http_status=code, error_type="auth_error")
                outcome.status, outcome.error_type, outcome.stop_reason = "error", "auth_error", "auth_error"
                return outcome
            if 400 <= code < 500:
                await _complete(session, attempt, status="failed", http_status=code, error_type="http_4xx")
                outcome.status, outcome.error_type = "error", "http_4xx"
                return outcome
            await _complete(session, attempt, status="failed", http_status=code, error_type="http_5xx")
            outcome.error_type = "http_5xx"
        # 5xx, timeout or connection error: same provider after 2 s, never a fallback
        if provider == PROVIDERS[0]:
            last_typesafe_429 = False
        if used[provider] >= MAX_ATTEMPTS_PER_PROVIDER:
            outcome.status = "error"
            return outcome
        await set_cooldown(session, provider, now() + timedelta(seconds=BACKOFF_S[0]), "backoff")


# ---- persistence and the run ------------------------------------------------------------


async def _record(session, run_id: str, ref: str, outcome: TaskOutcome, digest: Optional[str]) -> str:
    evaluation_id = str(uuid.uuid4())
    attempts = outcome.attempts
    tokens = sum((a.get("tokens") if a.get("tokens") is not None else a["reserved_tokens"]) for a in attempts)
    cost = sum((a.get("cost") if a.get("cost") is not None else a["reserved_cost_usd"]) for a in attempts)
    result = outcome.result or {}
    await session.execute(
        text(
            "INSERT INTO task_evaluations (id, task_ref, run_id, evaluator, rubric_version, status, raw_score, "
            "confidence, probabilities_json, legend_json, model, provider_used, input_hash, input_tokens, cost_usd, "
            "http_attempts, latency_ms, error_type, evaluated_at) VALUES (:id, :t, :run, 'jev', :r, :s, :score, :conf, "
            ":probs, :legend, :model, :prov, :h, :tok, :cost, :n, :lat, :err, :at)"
        ),
        {
            "id": evaluation_id, "t": ref, "run": run_id, "r": RUBRIC_VERSION, "s": outcome.status,
            "score": result.get("raw_score"), "conf": result.get("confidence"),
            "probs": json.dumps(result["probabilities"]) if result else None,
            "legend": json.dumps(result["legend"]) if result else None,
            "model": MODEL if attempts else None,
            "prov": result.get("provider_used") or (attempts[-1]["provider"] if attempts else None),
            "h": digest, "tok": tokens if attempts else None, "cost": cost if attempts else None,
            "n": len(attempts), "lat": sum(a.get("latency_ms", 0) for a in attempts) if attempts else None,
            "err": outcome.error_type, "at": _fmt(now()),
        },
    )
    await session.commit()
    if attempts:
        await _record_usage(session, evaluation_id, outcome, tokens)
    return evaluation_id


async def _record_usage(session, evaluation_id: str, outcome: TaskOutcome, tokens: int) -> None:
    from routes.events import EventIn, _insert_event, _prepare_event  # local import: routes import this module

    provider = (outcome.result or {}).get("provider_used") or outcome.attempts[-1]["provider"]
    body = EventIn(client_event_id=f"jev-{evaluation_id}", model=MODEL, provider=provider, role="evaluator",
                   prompt_tokens=tokens, completion_tokens=0, tags={"purpose": "evaluator", "evaluator": "jev"},
                   status="success" if outcome.status == "ok" else "error")
    event = await _prepare_event(body, EVALUATOR_PROJECT, session)
    await _insert_event(event, session)
    await session.commit()


async def run_evaluation(run_id: str, refs: list[str]) -> None:
    """Background task; never raises into a request."""
    state = features.current()
    api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("UPDATE evaluator_runs SET status = 'running', started_at = :at WHERE id = :id"),
                                  {"at": _fmt(now()), "id": run_id})
            await session.commit()
            status, stop_reason, retry_not_before = "done", None, None
            for index, ref in enumerate(refs):
                if index:
                    await sleep(TASK_SPACING_S)
                outcome = await evaluate_task(session, run_id, ref, state.budget, api_key)
                digest = None
                prompt = (await session.execute(text("SELECT prompt_text FROM tasks WHERE id = :id"),
                                                {"id": ref})).scalar()
                if prompt is not None:
                    digest = input_hash(prompt)
                if not (outcome.status == "skipped" and outcome.stop_reason == "budget_exceeded"):
                    await _record(session, run_id, ref, outcome, digest)
                if outcome.stop_reason:
                    status, stop_reason = "stopped", outcome.stop_reason
                    retry_not_before = _fmt(outcome.retry_not_before) if outcome.retry_not_before else None
                    break
            await session.execute(
                text("UPDATE evaluator_runs SET status = :s, stop_reason = :r, retry_not_before = :rnb, "
                     "finished_at = :at WHERE id = :id"),
                {"s": status, "r": stop_reason, "rnb": retry_not_before, "at": _fmt(now()), "id": run_id},
            )
            await session.commit()
    except Exception as exc:
        log.error("[JEV] run failed: %s", type(exc).__name__)
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("UPDATE evaluator_runs SET status = 'stopped', stop_reason = 'error', finished_at = :at "
                     "WHERE id = :id AND status IN ('queued', 'running')"),
                {"at": _fmt(now()), "id": run_id},
            )
            await session.commit()


async def recover_after_restart(session) -> None:
    """Startup: unfinished runs are aborted and reserved attempts become uncertain (kept at reserved cost)."""
    at = _fmt(now())
    await session.execute(
        text("UPDATE evaluator_runs SET status = 'aborted', stop_reason = 'restart', finished_at = :at "
             "WHERE status IN ('queued', 'running')"), {"at": at})
    await session.execute(text("UPDATE evaluator_attempts SET status = 'uncertain', completed_at = :at "
                               "WHERE status = 'reserved'"), {"at": at})
    await session.commit()
