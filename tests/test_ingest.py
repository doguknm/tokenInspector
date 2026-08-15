import hashlib
import os

from database import AsyncSessionLocal
from models import TokenEvent


async def test_ingest_validates_normalizes_and_hides_raw_prompt(client):
    response = await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes"},
        json={
            "client_event_id": "evt-1",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "model_requested": "claude-sonnet-latest",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "cache_read_tokens": 40,
            "cache_creation_tokens": 10,
            "input_tokens_include_cache": True,
            "reasoning_tokens": 99,
            "prompt_text": "private prompt",
            "tags": {"branch": "main", "api_token": "must-not-survive"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["duplicate"] is False
    assert body["cost_status"] == "priced"

    async with AsyncSessionLocal() as session:
        event = await session.get(TokenEvent, body["id"])
    assert event is not None
    assert event.prompt_tokens == 50
    assert event.reasoning_tokens == 20
    assert event.cost_status_reason == "reasoning_clamped"
    assert event.prompt_text is None
    assert event.prompt_length == len("private prompt")
    assert event.prompt_hash == hashlib.sha256(b"private prompt").hexdigest()
    assert "api_token" not in (event.tags_json or "")


async def test_project_status_and_auth_validation(client, monkeypatch):
    bad_project = await client.post(
        "/api/events",
        headers={"X-Project-Name": "Invalid Project"},
        json={"model": "claude-sonnet-4-6"},
    )
    assert bad_project.status_code == 422

    bad_tokens = await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes"},
        json={"model": "claude-sonnet-4-6", "prompt_tokens": -1},
    )
    assert bad_tokens.status_code == 422

    monkeypatch.setenv("INGEST_TOKEN", "local-secret")
    unauthorized = await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes"},
        json={"model": "claude-sonnet-4-6"},
    )
    assert unauthorized.status_code == 401
    authorized = await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes", "X-Ingest-Token": "local-secret"},
        json={"model": "claude-sonnet-4-6", "client_event_id": "auth-ok"},
    )
    assert authorized.status_code == 201
    monkeypatch.delenv("INGEST_TOKEN")


async def test_raw_prompt_is_explicit_opt_in(client, monkeypatch):
    monkeypatch.setenv("STORE_RAW_PROMPTS", "1")
    response = await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes"},
        json={"model": "claude-sonnet-4-6", "client_event_id": "raw-opt-in", "prompt_text": "allowed"},
    )
    assert response.status_code == 201
    async with AsyncSessionLocal() as session:
        event = await session.get(TokenEvent, response.json()["id"])
    assert event.prompt_text == "allowed"
    monkeypatch.setenv("STORE_RAW_PROMPTS", "0")
