from __future__ import annotations

from routes import analytics

START, END = "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z"
CC = {"runtime": "claude-code@hermes", "producer": "claude-code-hook"}


def event(cid, *, agent=None, complexity=None, method=None, model="zz-agent-unpriced", role="subagent", tags=None,
          session="s", turn="t", project=None):
    body = {"client_event_id": cid, "occurred_at": "2026-09-10T00:00:00Z", "model": model,
            "session_id": session, "turn_id": turn, "role": role, "tags": CC if tags is None else tags,
            "prompt_tokens": 10, "completion_tokens": 2}
    if agent is not None:
        body["agent"] = agent
    if complexity is not None:
        body.update(complexity=complexity, complexity_method=method)
    return body


async def post(client, body, project="proj"):
    response = await client.post("/api/events", json=body, headers={"X-Project-Name": project})
    assert response.status_code in (200, 201), response.text


async def test_agents_grouping_coverage_cost_and_global_tasks(client):
    await post(client, event("a", agent="main", complexity=1, method="cc-input-size-v1"))
    await post(client, event("b", agent="unknown", complexity=2, method="cc-input-size-v1"))
    await post(client, event("c", session="", turn="", agent=None))
    await post(client, event("eval", agent="main", role="evaluator"), project="token-inspector")
    await post(client, event("bad-attribution", agent="main", tags={"runtime": "bad", "producer": "claude-code-hook"}))

    response = await client.get("/api/analytics/agents", params={"from": START, "to": END})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contract_version"] == 1 and body["complete"] is True
    assert body["totals"]["calls"] == 3 and body["totals"]["tasks"] == 1
    assert sum(cell["tasks"] for cell in body["cells"]) == 2
    assert body["coverage"] == {"agent_unavailable_calls": 1, "agent_unknown_calls": 1,
                                "agent_custom_calls": 0, "complexity_unscored_calls": 1}
    assert body["totals"]["priced_cost_usd"] is None
    assert body["totals"]["unpriced_calls"] == body["totals"]["calls"]
    assert body["totals"]["avg_process_time_ms"] is None
    assert any(cell["agent"] is None and cell["complexity"] is None for cell in body["cells"])


async def test_agents_cell_bound_is_static_413(client, monkeypatch):
    monkeypatch.setattr(analytics, "AGENT_CELL_MAX", 1)
    await post(client, event("a", agent="main", complexity=1, method="cc-input-size-v1"))
    await post(client, event("b", agent="unknown", complexity=2, method="cc-input-size-v1"))
    response = await client.get("/api/analytics/agents", params={"from": START, "to": END})
    assert response.status_code == 413 and response.json() == {"detail": "result_too_large"}


async def test_task_agent_is_derived_from_its_counted_calls(client):
    first = event("task-agent-1", agent="main")
    second = event("task-agent-2")
    second["occurred_at"] = "2026-09-03T00:00:00Z"
    response = await client.post("/api/events/batch", json={"events": [first, second]},
                                 headers={"X-Project-Name": "proj"})
    assert response.status_code == 200, response.text
    item = (await client.get("/api/tasks?days=3650")).json()["items"][0]
    assert (item["agent"], item["agent_conflict"], item["agent_unavailable_calls"]) == ("main", False, 1)

    conflict = event("task-agent-3", agent="fork")
    conflict["occurred_at"] = "2026-09-04T00:00:00Z"
    assert (await client.post("/api/events", json=conflict, headers={"X-Project-Name": "proj"})).status_code == 201
    item = (await client.get("/api/tasks?days=3650")).json()["items"][0]
    assert (item["agent"], item["agent_conflict"], item["agent_unavailable_calls"]) == (None, True, 1)
