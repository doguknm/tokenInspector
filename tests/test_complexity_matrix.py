"""GET /api/analytics/complexity-matrix: tier x model volume, per-call/per-task tokens, cost, filters."""

import itertools

H = {"X-Project-Name": "hermes"}
RS1 = "request-shape-v1"
_ids = itertools.count()


def _ev(model="claude-sonnet-4-6", tier=2, turn="t1", session="s1", **extra):
    body = {"client_event_id": f"cm-{next(_ids)}", "model": model, "complexity": tier, "complexity_method": RS1,
            "session_id": session, "turn_id": turn, "prompt_tokens": 100, "completion_tokens": 10,
            "cache_read_tokens": 1000, "cache_creation_tokens": 0}
    body.update(extra)
    return body


async def _post(client, events, headers=H):
    response = await client.post("/api/events/batch", headers=headers, json={"events": events})
    assert response.status_code == 200, response.text


async def _matrix(client, query=""):
    response = await client.get(f"/api/analytics/complexity-matrix?days=30{query}")
    assert response.status_code == 200, response.text
    return response.json()


def _cell(data, tier, model):
    return next(c for c in data["cells"] if c["complexity"] == tier and c["model"] == model)


async def test_counts_calls_and_distinct_tasks_across_calls(client):
    await _post(client, [
        _ev(turn="t1", process_time_ms=100, ttft_ms=10),
        _ev(turn="t1", process_time_ms=300, ttft_ms=30),
        _ev(turn="t1", status="error", process_time_ms=200),
        _ev(turn="t2", prompt_tokens=400, cache_creation_tokens=50),
        _ev(turn=None, session=None, prompt_tokens=7),  # a call with no task
        _ev(model="claude-haiku-4-5", turn="t1"),        # same task, other model
    ])
    data = await _matrix(client)
    assert data["method"] == RS1 and data["tier_source"] == "llm_call"
    sonnet = _cell(data, 2, "claude-sonnet-4-6")
    assert sonnet["calls"] == 5 and sonnet["tasks"] == 2 and sonnet["calls_without_task"] == 1
    assert sonnet["prompt_tokens"] == 100 * 3 + 400 + 7
    assert sonnet["cache_read_tokens"] == 5000 and sonnet["cache_creation_tokens"] == 50
    assert sonnet["per_call"]["prompt_tokens"] == round(707 / 5)
    assert sonnet["per_task"]["prompt_tokens"] == 350  # (300 + 400) / 2, the task-less call excluded
    assert sonnet["per_task"]["cache_creation_tokens"] == 25
    assert sonnet["avg_process_time_ms"] == 200 and sonnet["avg_ttft_ms"] == 20
    assert sonnet["error_count"] == 1 and sonnet["error_rate"] == 0.2
    assert sonnet["priced_calls"] == 5 and sonnet["unpriced_calls"] == 0 and sonnet["cost_complete"]
    assert sonnet["priced_cost_usd"] > 0 and sonnet["avg_priced_cost_per_task"] > 0
    assert sonnet["completion"] == {"session_end": 0, "next_task": 1, "inferred": 0, "open": 1, "unknown": 0}
    assert sonnet["low_sample"] is True
    # The tier counts task t1 once although it used two models.
    tier = data["tiers"][0]
    assert tier["complexity"] == 2 and tier["calls"] == 6 and tier["tasks"] == 2
    assert data["models"] == ["claude-haiku-4-5", "claude-sonnet-4-6"]


async def test_unpriced_cost_is_null_not_zero(client):
    await _post(client, [_ev(model="unknown-model", turn=f"u{i}") for i in range(3)]
                + [_ev(turn="p1"), _ev(model="unknown-model", turn="p1")])
    data = await _matrix(client)
    unpriced = _cell(data, 2, "unknown-model")
    assert unpriced["unpriced_calls"] == 4 and unpriced["priced_calls"] == 0
    assert unpriced["priced_cost_usd"] is None and unpriced["avg_priced_cost_per_task"] is None
    assert unpriced["priced_cost_per_1k_output"] is None and unpriced["cost_complete"] is False
    tier = data["tiers"][0]
    assert tier["unpriced_calls"] == 4 and tier["priced_calls"] == 1 and tier["cost_complete"] is False


async def test_filters_model_project_method(client):
    await _post(client, [_ev(tier=1, turn="a"), _ev(model="claude-haiku-4-5", tier=3, turn="b"),
                         _ev(tier=4, turn="c", complexity_method="legacy-ai"),
                         _ev(tier=5, turn="d", complexity_method=None, tags={"complexity_version": RS1})])
    await _post(client, [_ev(tier=2, turn="x")], headers={"X-Project-Name": "other"})

    data = await _matrix(client)
    assert data["methods"] == ["legacy-ai", RS1]
    assert data["projects"] == ["hermes", "other"]
    assert [t["complexity"] for t in data["tiers"]] == [1, 2, 3, 5]  # tag-only method counts as RS1

    only_haiku = await _matrix(client, "&model=claude-haiku-4-5")
    assert [(c["complexity"], c["model"]) for c in only_haiku["cells"]] == [(3, "claude-haiku-4-5")]
    assert only_haiku["models"] == ["claude-haiku-4-5", "claude-sonnet-4-6"]  # options ignore the model filter
    both = await _matrix(client, "&model=claude-haiku-4-5&model=claude-sonnet-4-6")
    assert len(both["cells"]) == 4

    other = await _matrix(client, "&project=other")
    assert [(c["complexity"], c["model"]) for c in other["cells"]] == [(2, "claude-sonnet-4-6")]

    legacy = await _matrix(client, "&method=legacy-ai")
    assert [c["complexity"] for c in legacy["cells"]] == [4]


async def test_lowest_cost_and_latency_skip_low_sample(client):
    events = []
    for i in range(5):
        events.append(_ev(model="claude-sonnet-4-6", tier=3, turn=f"s{i}", process_time_ms=900))
        events.append(_ev(model="claude-haiku-4-5", tier=3, turn=f"h{i}", process_time_ms=400))
    events.append(_ev(model="claude-haiku-3-5", tier=3, turn="few", process_time_ms=10))  # low sample
    await _post(client, events)
    data = await _matrix(client)
    haiku, sonnet, few = (_cell(data, 3, m) for m in ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-haiku-3-5"))
    assert haiku["lowest_latency"] and haiku["lowest_cost_per_task"]
    assert not sonnet["lowest_latency"] and not sonnet["lowest_cost_per_task"]
    assert few["low_sample"] and not few["lowest_latency"]
    assert data["low_sample_tasks"] == 5


async def test_existing_complexity_endpoints_unchanged(client):
    await _post(client, [_ev(tier=2, turn="a")])
    rows = (await client.get("/api/analytics/by-complexity?days=30")).json()
    assert set(rows[0]) == {"complexity", "avg_cost_usd", "avg_tokens", "event_count", "priced_event_count"}
