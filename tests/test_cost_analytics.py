import subprocess

import pytest


async def test_cache_aware_pricing_and_reasoning_not_double_charged(client):
    response = await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes"},
        json={
            "model": "claude-sonnet-4-6",
            "client_event_id": "priced",
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1_000_000,
            "cache_read_tokens": 1_000_000,
            "cache_creation_tokens": 1_000_000,
            "reasoning_tokens": 500_000,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["cost_status"] == "priced"
    assert body["estimated_cost_usd"] == pytest.approx(3 + 15 + 0.3 + 3.75)


async def test_unpriced_estimated_and_alias_resolution(client):
    unpriced = await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes"},
        json={"model": "missing-model", "client_event_id": "unpriced", "prompt_tokens": 10},
    )
    assert unpriced.json()["cost_status"] == "unpriced"
    assert unpriced.json()["estimated_cost_usd"] is None

    estimated = await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes"},
        json={"model": "claude-sonnet-4-6", "client_event_id": "estimated", "total_tokens": 1_000_000},
    )
    assert estimated.json()["cost_status"] == "estimated"
    assert estimated.json()["cost_status_reason"] == "total_tokens_only"

    alias_response = await client.post(
        "/api/settings/aliases",
        json={"alias": "claude-sonnet-latest", "model": "claude-sonnet-4-6"},
    )
    assert alias_response.status_code == 200
    aliased = await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes"},
        json={"model": "claude-sonnet-latest", "client_event_id": "alias", "prompt_tokens": 1000},
    )
    assert aliased.json()["pricing_model"] == "claude-sonnet-4-6"
    assert aliased.json()["cost_status"] == "priced"


async def test_analytics_are_sql_aggregated_and_cost_status_separated(client):
    events = [
        {"model": "claude-sonnet-4-6", "client_event_id": "a1", "provider": "anthropic", "prompt_tokens": 10, "completion_tokens": 5},
        {"model": "unknown-model", "client_event_id": "a2", "provider": "unknown", "prompt_tokens": 7, "completion_tokens": 3},
    ]
    response = await client.post(
        "/api/events/batch",
        headers={"X-Project-Name": "hermes"},
        json={"events": events},
    )
    assert response.status_code == 200
    summary = (await client.get("/api/analytics/summary?days=30")).json()
    assert summary["total_events"] == 2
    assert summary["total_prompt_tokens"] == 17
    assert summary["total_completion_tokens"] == 8
    assert summary["unpriced_event_count"] == 1
    by_provider = (await client.get("/api/analytics/by-provider?days=30")).json()
    assert {row["provider"] for row in by_provider} == {"anthropic", "unknown"}
    timeseries = (await client.get("/api/analytics/timeseries?days=30&granularity=hour")).json()
    assert len(timeseries) == 1


async def test_complexity_analytics_include_unpriced_events(client):
    response = await client.post(
        "/api/events/batch",
        headers={"X-Project-Name": "pegadocrag"},
        json={
            "events": [
                {
                    "model": "unknown-model",
                    "client_event_id": "complexity-unpriced-1",
                    "event_type": "llm_request",
                    "complexity": 3,
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                },
                {
                    "model": "unknown-model",
                    "client_event_id": "complexity-unpriced-2",
                    "event_type": "llm_request",
                    "complexity": 3,
                    "prompt_tokens": 200,
                    "completion_tokens": 40,
                },
            ]
        },
    )
    assert response.status_code == 200

    rows = (await client.get("/api/analytics/by-complexity?days=30&project=pegadocrag")).json()

    assert rows == [
        {
            "complexity": 3,
            "avg_cost_usd": None,
            "avg_tokens": 180,
            "event_count": 2,
            "priced_event_count": 0,
        }
    ]


async def test_project_inventory_includes_repositories_without_events(client, monkeypatch, tmp_path):
    active = tmp_path / "folder-name"
    inactive = tmp_path / "inactive-folder"
    subprocess.run(["git", "init", "-q", str(active)], check=True)
    subprocess.run(["git", "init", "-q", str(inactive)], check=True)
    subprocess.run(
        ["git", "-C", str(active), "remote", "add", "origin", "git@example.invalid:team/canonical-repo.git"],
        check=True,
    )
    monkeypatch.setenv("TOKEN_INSPECTOR_PROJECT_ROOTS", str(tmp_path))

    await client.post(
        "/api/events",
        headers={"X-Project-Name": "canonical-repo"},
        json={"model": "unknown-model", "client_event_id": "inventory-event", "prompt_tokens": 10},
    )

    response = await client.get("/api/analytics/project-inventory?days=30")

    assert response.status_code == 200
    rows = response.json()
    assert rows == [
        {
            "project_name": "canonical-repo",
            "directory_name": "folder-name",
            "discovery_source": "git_remote",
            "workspace_id": rows[0]["workspace_id"],
            "has_telemetry": True,
            "event_count": 1,
            "total_tokens": 10,
            "last_activity_at": rows[0]["last_activity_at"],
        },
        {
            "project_name": "inactive-folder",
            "directory_name": "inactive-folder",
            "discovery_source": "git_root",
            "workspace_id": rows[1]["workspace_id"],
            "has_telemetry": False,
            "event_count": 0,
            "total_tokens": 0,
            "last_activity_at": None,
        },
    ]
    assert all("/" not in row["workspace_id"] for row in rows)


async def test_recost_dry_run_does_not_mutate(client):
    await client.post(
        "/api/events",
        headers={"X-Project-Name": "hermes"},
        json={"model": "future-model", "client_event_id": "recost", "prompt_tokens": 1_000_000},
    )
    await client.post(
        "/api/settings/pricing",
        json={"model": "future-model", "input_price_per_1m": 2, "output_price_per_1m": 4},
    )
    dry = (await client.post("/api/settings/recost?dry_run=true")).json()
    assert dry["changed"] == 1
    unpriced = (await client.get("/api/settings/unpriced-models")).json()
    assert unpriced[0]["model"] == "future-model"
    applied = (await client.post("/api/settings/recost?dry_run=false")).json()
    assert applied["changed"] == 1
    assert (await client.get("/api/settings/unpriced-models")).json() == []
