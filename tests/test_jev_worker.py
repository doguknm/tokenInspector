"""JEV worker (AC6a-AC6g): gates, atomic runs, durable reservations, Retry-After, fallback, validation."""

import copy
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

import features
import jev_scorer
from database import AsyncSessionLocal

TOKEN = "j" * 24
AUTH = {"X-Ingest-Token": TOKEN}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "jev_response_2026-09-27.json").read_text(encoding="utf-8"))
OK_BODY = FIXTURE["response"]
START = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = START
        self.sleeps = []

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))
        self.t += timedelta(seconds=seconds)


class Gateway:
    """Scripted gateway: one (status, body, headers) per call; records provider and DB state at call time."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    async def __call__(self, body, api_key):
        async with AsyncSessionLocal() as session:
            reserved = (await session.execute(text(
                "SELECT COUNT(*) FROM evaluator_attempts WHERE status = 'reserved'"))).scalar()
        self.calls.append({"provider": body["providerOptions"]["gateway"]["only"][0], "reserved_before": reserved,
                           "state": body["state"]})
        status, payload, headers = self.script.pop(0) if self.script else (200, OK_BODY, {})
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(status, json=payload, headers=headers)


def ok_from(provider):
    body = copy.deepcopy(OK_BODY)
    body["provider_metadata"]["gateway"]["routing"]["finalProvider"] = provider
    return (200, body, {})


def rate_limited(retry_after=None):
    return (429, {"error": {"type": "rate_limit_exceeded"}}, {"retry-after": retry_after} if retry_after else {})


@pytest.fixture
def jev(client, monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    features.refresh()
    clock = Clock()
    monkeypatch.setattr(jev_scorer, "now", clock.now)
    monkeypatch.setattr(jev_scorer, "sleep", clock.sleep)
    yield clock
    features.refresh()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _task(ref, prompt="Add a tasks table with tests.", project="hermes"):
    async with AsyncSessionLocal() as session:
        await session.execute(text(
            "INSERT INTO tasks (id, project_name, session_id, turn_id, first_seen_at, last_seen_at, prompt_text, "
            "prompt_captured_at, prompt_expires_at, created_at, updated_at) VALUES (:id, :p, 's', :id, :n, :n, :t, :n, "
            ":e, :n, :n)"), {"id": ref, "p": project, "t": prompt, "n": _iso(START - timedelta(days=1)),
                             "e": _iso(datetime.now(timezone.utc) + timedelta(days=20))})
        await session.commit()
    return ref


async def _one(sql, **params):
    async with AsyncSessionLocal() as session:
        return (await session.execute(text(sql), params)).all()


async def _evaluate(client, refs):
    return await client.post("/api/tasks/evaluate", json={"task_refs": refs}, headers=AUTH)


A, B = "a" * 32, "b" * 32


# --- AC6a ------------------------------------------------------------------------------------


async def test_disabled_jev_makes_no_calls(client, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    gateway = Gateway()
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    body = (await _evaluate(client, [A])).json()
    assert body == {"run_id": None, "enabled": False, "queued": 0,
                    "skipped": [{"task_ref": A, "reason": "jev_disabled"}], "retry_not_before": None}
    assert gateway.calls == []
    status = (await client.get("/api/tasks/evaluator-status")).json()
    assert status["enabled"] is False and status["disabled_reason"] == "not_enabled"


# --- AC6e ------------------------------------------------------------------------------------


def test_parser_reads_the_real_fixture():
    parsed = jev_scorer.validate(OK_BODY)
    assert parsed["raw_score"] == 2.2 and parsed["confidence"] == 0.83
    assert parsed["probabilities"] == [0, 0, 0.8, 0.2, 0]
    assert parsed["legend"] == jev_scorer.CRITERIA
    assert (parsed["input_tokens"], parsed["cost_usd"], parsed["provider_used"]) == (446, 0.000018732, "typesafe-ai")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(score=math.inf),
        lambda d: d.update(score=4.5),
        lambda d: d.update(score=True),
        lambda d: d.update(confidence=-0.1),
        lambda d: d.update(type="choice"),
        lambda d: d["probabilities"].pop("4"),
        lambda d: d["probabilities"].update({"0": 0.5}),
        lambda d: d["probabilities"].update({"0": -0.2, "2": 1.0}),
        lambda d: d["legend"].update({"2": "something else"}),
        lambda d: d.update(legend=["a", "b", "c", "d"]),
    ],
)
def test_validation_rejects_bad_responses(mutate):
    body = copy.deepcopy(OK_BODY)
    mutate(body["answers"]["difficulty"])
    with pytest.raises(jev_scorer.InvalidResponse):
        jev_scorer.validate(body)


def test_retry_after_parsing():
    at = START
    assert jev_scorer.parse_retry_after("7", at) == 7.0
    assert jev_scorer.parse_retry_after("Sun, 27 Sep 2026 12:02:00 GMT", at) == 120.0
    assert jev_scorer.parse_retry_after("Sun, 27 Sep 2026 11:00:00 GMT", at) == 0.0
    assert jev_scorer.parse_retry_after("soon", at) is None and jev_scorer.parse_retry_after(None, at) is None


# --- happy path, AC6f, AC6c reservation ----------------------------------------------------------


async def test_successful_run_records_evaluation_usage_and_skips_rescoring(client, jev, monkeypatch):
    gateway = Gateway(ok_from("typesafe-ai"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    response = await _evaluate(client, [A])
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    summary = (await client.get(f"/api/tasks/evaluator-runs/{run_id}")).json()
    assert (summary["status"], summary["ok"], summary["attempted"]) == ("done", 1, 1)
    assert gateway.calls[0]["reserved_before"] == 1  # the reservation was committed before the call
    assert "prompt" not in json.dumps(gateway.calls[0]["provider"])
    ((status, raw, provider, cost, attempts),) = await _one(
        "SELECT status, raw_score, provider_used, cost_usd, http_attempts FROM task_evaluations WHERE task_ref = :t", t=A)
    assert (status, raw, provider, attempts) == ("ok", 2.2, "typesafe-ai", 1) and cost == pytest.approx(0.000018732)
    assert await _one("SELECT status, actual_input_tokens FROM evaluator_attempts") == [("succeeded", 446)]
    usage = await _one("SELECT project_name, model, role, task_id, session_id, turn_id, prompt_tokens FROM token_events")
    assert usage == [("token-inspector", "typesafe-ai/jev", "evaluator", None, None, None, 446)]
    assert await _one("SELECT COUNT(*) FROM tasks WHERE project_name = 'token-inspector'") == [(0,)]
    again = (await _evaluate(client, [A])).json()
    assert again["queued"] == 0 and again["skipped"] == [{"task_ref": A, "reason": "already_scored"}]
    assert len(gateway.calls) == 1


async def test_skip_reasons_at_request_time(client, jev, monkeypatch):
    monkeypatch.setattr(jev_scorer, "post_evaluation", Gateway())
    await _task(A, project="token-inspector")
    await _task(B)
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE tasks SET prompt_expires_at = '2000-01-01T00:00:00.000000Z' WHERE id = :id"),
                              {"id": B})
        await session.commit()
    body = (await _evaluate(client, [A, B, "c" * 32])).json()
    assert {s["task_ref"]: s["reason"] for s in body["skipped"]} == {
        A: "evaluator_task", B: "prompt_expired", "c" * 32: "not_found"}
    assert body["run_id"] is None


# --- AC6b ------------------------------------------------------------------------------------------


async def test_second_run_is_refused_and_restart_recovery(client, jev, monkeypatch):
    monkeypatch.setattr(jev_scorer, "post_evaluation", Gateway())
    await _task(A)
    async with AsyncSessionLocal() as session:
        await session.execute(text("INSERT INTO evaluator_runs (id, evaluator, status, requested_count, queued_count, "
                                   "queued_at) VALUES ('busy', 'jev', 'running', 1, 1, '2026-09-27T00:00:00Z')"))
        await session.execute(text("INSERT INTO evaluator_attempts (id, run_id, task_ref, provider, status, "
                                   "reserved_tokens, reserved_cost_usd, started_at) VALUES ('att', 'busy', :t, "
                                   "'typesafe-ai', 'reserved', 2000, 0.000084, :at)"), {"t": A, "at": _iso(START)})
        await session.commit()
    conflict = await _evaluate(client, [A])
    assert conflict.status_code == 409 and conflict.json()["detail"] == "evaluation_in_progress"
    async with AsyncSessionLocal() as session:
        await jev_scorer.recover_after_restart(session)
    assert await _one("SELECT status, stop_reason FROM evaluator_runs") == [("aborted", "restart")]
    assert await _one("SELECT status FROM evaluator_attempts") == [("uncertain",)]
    status = (await client.get("/api/tasks/evaluator-status")).json()
    assert status["spent_today_usd"] == pytest.approx(0.000084)  # uncertain keeps its reserved cost


# --- AC6c budget ---------------------------------------------------------------------------------


async def test_budget_ceiling_stops_before_the_call(client, jev, monkeypatch):
    monkeypatch.setenv("JEV_DAILY_BUDGET_USD", "0.00001")
    features.refresh()
    gateway = Gateway()
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    run_id = (await _evaluate(client, [A])).json()["run_id"]
    summary = (await client.get(f"/api/tasks/evaluator-runs/{run_id}")).json()
    assert (summary["status"], summary["stop_reason"]) == ("stopped", "budget_exceeded")
    assert gateway.calls == [] and await _one("SELECT COUNT(*) FROM evaluator_attempts") == [(0,)]


async def test_daily_call_ceiling_counts_every_attempt(client, jev, monkeypatch):
    monkeypatch.setenv("JEV_MAX_CALLS_PER_DAY", "2")
    features.refresh()
    gateway = Gateway(rate_limited("1"), ok_from("typesafe-ai"), ok_from("typesafe-ai"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    await _task(B)
    run_id = (await _evaluate(client, [A, B])).json()["run_id"]
    summary = (await client.get(f"/api/tasks/evaluator-runs/{run_id}")).json()
    assert len(gateway.calls) == 2  # the 429 counted as a call
    assert (summary["status"], summary["stop_reason"], summary["ok"]) == ("stopped", "budget_exceeded", 1)


# --- AC6d Retry-After and routing ------------------------------------------------------------------


async def _run(client, refs):
    run_id = (await _evaluate(client, refs)).json()["run_id"]
    return (await client.get(f"/api/tasks/evaluator-runs/{run_id}")).json()


async def test_short_retry_after_is_honoured_exactly_on_the_same_provider(client, jev, monkeypatch):
    gateway = Gateway(rate_limited("5"), ok_from("typesafe-ai"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    summary = await _run(client, [A])
    assert [c["provider"] for c in gateway.calls] == ["typesafe-ai", "typesafe-ai"]
    assert 5.0 in jev.sleeps and summary["ok"] == 1
    assert await _one("SELECT retry_after_s FROM evaluator_attempts WHERE http_status = 429") == [(5.0,)]


async def test_long_retry_after_falls_back_immediately(client, jev, monkeypatch):
    gateway = Gateway(rate_limited("120"), ok_from("digitalocean"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    summary = await _run(client, [A])
    assert [c["provider"] for c in gateway.calls] == ["typesafe-ai", "digitalocean"]
    assert all(s < 120 for s in jev.sleeps) and summary["ok"] == 1
    assert await _one("SELECT provider_used FROM task_evaluations") == [("digitalocean",)]


async def test_missing_or_malformed_retry_after_uses_backoff(client, jev, monkeypatch):
    gateway = Gateway(rate_limited(), rate_limited("soon"), ok_from("digitalocean"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    summary = await _run(client, [A])
    assert [c["provider"] for c in gateway.calls] == ["typesafe-ai", "typesafe-ai", "digitalocean"]
    assert jev.sleeps[0] == 2.0 and summary["ok"] == 1


async def test_digitalocean_long_wait_defers_the_run(client, jev, monkeypatch):
    gateway = Gateway(rate_limited("120"), rate_limited("300"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    await _task(B)
    summary = await _run(client, [A, B])
    assert (summary["status"], summary["stop_reason"], summary["deferred"]) == ("stopped", "deferred_rate_limited", 1)
    assert summary["retry_not_before"] == _iso(START + timedelta(seconds=300))
    assert len(gateway.calls) == 2  # task B never attempted


async def test_5xx_retries_same_provider_without_fallback(client, jev, monkeypatch):
    gateway = Gateway((502, {"x": 1}, {}), (503, {"x": 1}, {}))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    summary = await _run(client, [A])
    assert [c["provider"] for c in gateway.calls] == ["typesafe-ai", "typesafe-ai"]
    assert summary["error"] == 1
    assert await _one("SELECT error_type FROM task_evaluations") == [("http_5xx",)]


async def test_auth_error_stops_the_run(client, jev, monkeypatch):
    gateway = Gateway((401, {"x": 1}, {}))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    await _task(B)
    summary = await _run(client, [A, B])
    assert (summary["status"], summary["stop_reason"]) == ("stopped", "auth_error") and len(gateway.calls) == 1


async def test_invalid_response_stores_no_numbers(client, jev, monkeypatch):
    bad = copy.deepcopy(OK_BODY)
    bad["answers"]["difficulty"]["score"] = 9
    monkeypatch.setattr(jev_scorer, "post_evaluation", Gateway((200, bad, {})))
    await _task(A)
    await _run(client, [A])
    assert await _one("SELECT status, error_type, raw_score FROM task_evaluations") == [
        ("error", "invalid_response", None)]


# --- AC6g persisted cooldowns --------------------------------------------------------------------


async def test_cooldown_applies_to_later_tasks_and_requests(client, jev, monkeypatch):
    gateway = Gateway(rate_limited("120"), ok_from("digitalocean"), ok_from("digitalocean"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task(A)
    await _task(B)
    await _run(client, [A, B])
    assert [c["provider"] for c in gateway.calls] == ["typesafe-ai", "digitalocean", "digitalocean"]
    cooldowns = (await client.get("/api/tasks/evaluator-status")).json()["provider_cooldowns"]
    assert cooldowns == [{"provider": "typesafe-ai", "not_before": _iso(START + timedelta(seconds=120))}]

    async with AsyncSessionLocal() as session:  # both providers cooling beyond the allowance, e.g. after a restart
        await jev_scorer.set_cooldown(session, "digitalocean", START + timedelta(seconds=600), "retry_after")
        await session.execute(text("DELETE FROM task_evaluations"))
        await session.commit()
    body = (await _evaluate(client, [A])).json()
    assert body["run_id"] is None and body["queued"] == 0
    assert body["retry_not_before"] is not None
