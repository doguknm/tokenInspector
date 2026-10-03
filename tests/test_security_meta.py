"""§0 security guards, feature gates and /api/meta (AC11a, AC11b, AC11c, AC11d, AC6a flags)."""

import logging

import httpx
import pytest
from fastapi import Depends, FastAPI

import features
from auth import require_sensitive_auth
from main import app, lifespan

TOKEN = "t" * 24
GOOD = {"INGEST_TOKEN": TOKEN, "AI_GATEWAY_API_KEY": "key", "TASK_PROMPT_ALLOWED_PROJECTS": "hermes"}


def _sensitive_app():
    test_app = FastAPI()

    @test_app.get("/sensitive", dependencies=[Depends(require_sensitive_auth)])
    async def sensitive():
        return {"ok": True}

    return test_app


async def _get(test_app, headers=None, base_url="http://test"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url=base_url) as c:
        return await c.get("/sensitive", headers=headers or {})


async def test_sensitive_auth_requires_configured_token(monkeypatch):
    monkeypatch.delenv("INGEST_TOKEN", raising=False)
    response = await _get(_sensitive_app(), {"X-Ingest-Token": TOKEN})
    assert (response.status_code, response.json()["detail"]) == (403, "auth_not_configured")


async def test_sensitive_auth_rejects_missing_or_wrong_token_and_foreign_origin(monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    test_app = _sensitive_app()
    assert (await _get(test_app)).status_code == 401
    assert (await _get(test_app, {"X-Ingest-Token": "wrong"})).status_code == 401
    foreign = await _get(test_app, {"X-Ingest-Token": TOKEN, "Origin": "http://evil.example"})
    assert (foreign.status_code, foreign.json()["detail"]) == (403, "origin_not_allowed")
    assert (await _get(test_app, {"X-Ingest-Token": TOKEN, "Origin": "http://localhost:8100"})).status_code == 200
    assert (await _get(test_app, {"X-Ingest-Token": TOKEN})).status_code == 200
    monkeypatch.setenv("TOKEN_INSPECTOR_ALLOWED_ORIGINS", "http://evil.example")
    assert (await _get(test_app, {"X-Ingest-Token": TOKEN, "Origin": "http://evil.example"})).status_code == 200


async def test_foreign_host_is_rejected(client):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evil.example") as c:
        assert (await c.get("/api/meta")).status_code == 400
    assert (await client.get("/api/meta")).status_code == 200


async def test_meta_preflight_with_flags_off_and_valid_config(client, monkeypatch):
    for name, value in GOOD.items():
        monkeypatch.setenv(name, value)
    features.refresh()
    body = (await client.get("/api/meta")).json()
    assert body == {
        "schema_version": 13,
        "task_prompt_capture": False,
        "task_prompt_capture_disabled_reason": "not_enabled",
        "jev_enabled": False,
        "jev_disabled_reason": "not_enabled",
        "config_errors": [],
        "task_prompt_allowlist": "configured",
    }


async def test_meta_lists_config_errors_even_with_flags_off(client, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "short")
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("JEV_DAILY_BUDGET_USD", "5")  # above the monthly ceiling
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "hermes")  # the empty-list error is covered separately
    features.refresh()
    body = (await client.get("/api/meta")).json()
    assert body["task_prompt_capture_disabled_reason"] == "not_enabled"
    assert {(e["feature"], e["error"]) for e in body["config_errors"]} == {
        ("capture", "ingest_token_missing"),
        ("jev", "credentials_missing"),
        ("jev", "ingest_token_missing"),
        ("jev", "invalid_budget_config"),
    }


@pytest.mark.parametrize(
    ("env", "capture", "jev"),
    [
        ({"STORE_TASK_PROMPTS": "1", "JEV_ENABLED": "1", **GOOD}, (True, None), (True, None)),
        ({"STORE_TASK_PROMPTS": "1", "JEV_ENABLED": "1"}, (False, "ingest_token_missing"), (False, "credentials_missing")),
        ({"STORE_TASK_PROMPTS": "1", "TASK_PROMPT_PURGE_INTERVAL_S": "0", **GOOD}, (False, "invalid_purge_interval"), (False, "not_enabled")),
        ({"STORE_TASK_PROMPTS": "1", "TASK_PROMPT_PURGE_INTERVAL_S": "99999", **GOOD}, (False, "invalid_purge_interval"), (False, "not_enabled")),
        ({"JEV_ENABLED": "1", "JEV_MAX_CALLS_PER_DAY": "0", **GOOD}, (False, "not_enabled"), (False, "invalid_budget_config")),
        ({"JEV_ENABLED": "1", "JEV_MONTHLY_BUDGET_USD": "nan", **GOOD}, (False, "not_enabled"), (False, "invalid_budget_config")),
        ({"JEV_ENABLED": "yes", "INGEST_TOKEN": "short", "AI_GATEWAY_API_KEY": "k"}, (False, "not_enabled"), (False, "ingest_token_missing")),
    ],
)
def test_feature_gates(env, capture, jev):
    state = features.evaluate(env)
    assert (state.capture_enabled, state.capture_disabled_reason) == capture
    assert (state.jev_enabled, state.jev_disabled_reason) == jev


async def test_refused_feature_logs_error_but_service_starts(monkeypatch, caplog):
    monkeypatch.setenv("STORE_TASK_PROMPTS", "1")
    monkeypatch.delenv("INGEST_TOKEN", raising=False)
    caplog.set_level(logging.INFO)
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            body = (await c.get("/api/meta")).json()
    assert body["task_prompt_capture"] is False
    assert body["task_prompt_capture_disabled_reason"] == "ingest_token_missing"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "[SECURITY]" in r.getMessage()]
    assert errors and "task prompt capture disabled: ingest_token_missing" in errors[0].getMessage()
    features.refresh()
