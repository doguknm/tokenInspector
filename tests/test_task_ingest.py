"""Live task derivation at ingest (AC4c, AC4d, AC4e, AC10a, AC10d), prompt capture (AC5b, AC11c), composition."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import features
import task_store
from database import AsyncSessionLocal

TOKEN = "i" * 24
H = {"X-Project-Name": "hermes"}
RS1 = {"complexity_version": "request-shape-v1"}


def _ts(second: int, minute: int = 0) -> str:
    return f"2026-09-20T10:{minute:02d}:{second:02d}.000000Z"


def _ev(cid, turn="s1:t:1", **extra):
    body = {"client_event_id": cid, "model": "m", "session_id": "s1", "task_id": "gw", "turn_id": turn,
            "occurred_at": _ts(1), "prompt_tokens": 1}
    body.update(extra)
    return body


async def _tasks():
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text("SELECT * FROM tasks ORDER BY first_seen_at, id"))).mappings().all()
    return [dict(r) for r in rows]


async def _post(client, *events, headers=H):
    for event in events:
        response = await client.post("/api/events", json=event, headers=headers)
        assert response.status_code in (200, 201), response.text


def _enable_capture(monkeypatch):
    monkeypatch.setenv("STORE_TASK_PROMPTS", "1")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    features.refresh()
    return {**H, "X-Ingest-Token": TOKEN}


# --- AC4c ---------------------------------------------------------------------------


async def test_llm_request_with_turn_creates_one_ingest_task(client):
    await _post(client, _ev("a", complexity=3, complexity_method="request-shape-v1"))
    (task,) = await _tasks()
    assert task["id"] == task_store.task_ref("hermes", "s1", "s1:t:1")
    assert (task["source"], task["source_task_id"], task["turn_id"]) == ("ingest", "gw", "s1:t:1")
    assert (task["start_complexity"], task["start_complexity_method"]) == (3, "request-shape-v1")
    assert task["hierarchy_status"] == "unknown"


async def test_complexity_without_method_never_sets_start(client):
    await _post(client, _ev("a", complexity=4))
    (task,) = await _tasks()
    assert (task["start_complexity"], task["start_complexity_method"]) == (None, None)


async def test_out_of_order_earlier_event_moves_first_seen_and_start_but_tool_call_never(client):
    await _post(client, _ev("late", occurred_at=_ts(30), complexity=2, tags=RS1))
    await _post(client, _ev("tool", occurred_at=_ts(1), event_type="tool_call", complexity=5, tags=RS1))
    (task,) = await _tasks()
    assert task["first_seen_at"] == _ts(1)
    assert task["start_complexity"] == 2
    await _post(client, _ev("early", occurred_at=_ts(5), complexity=4, tags=RS1))
    (task,) = await _tasks()
    assert (task["first_seen_at"], task["last_seen_at"]) == (_ts(1), _ts(30))
    assert (task["start_complexity"], task["start_complexity_event_at"]) == (4, _ts(5))


async def test_duplicate_replay_and_turnless_events_change_nothing(client):
    await _post(client, _ev("a", complexity=3, tags=RS1))
    before = await _tasks()
    response = await client.post("/api/events", json=_ev("a", occurred_at=_ts(59), complexity=1, tags=RS1), headers=H)
    assert response.status_code == 200
    assert await _tasks() == before
    await _post(client, _ev("b", turn=None), _ev("c", turn="unknown"), _ev("d", turn="", session_id=None))
    assert len(await _tasks()) == 1


async def test_concurrent_distinct_events_create_exactly_one_task(client):
    events = [_ev(f"c{i}", occurred_at=_ts(i % 60)) for i in range(20)]
    responses = await asyncio.gather(*(client.post("/api/events", json=e, headers=H) for e in events))
    assert {r.status_code for r in responses} == {201}
    assert len(await _tasks()) == 1


# --- AC4d / AC4e (ingest side; same rule as the backfill) ------------------------------


async def test_same_timestamp_picks_lowest_event_id(client):
    await _post(client, _ev("x", complexity=2, tags=RS1), _ev("y", complexity=4, tags=RS1))
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text("SELECT id, complexity FROM token_events ORDER BY id"))).all()
    (task,) = await _tasks()
    assert (task["start_complexity_event_id"], task["start_complexity"]) == (rows[0][0], rows[0][1])


async def test_non_approved_methods_are_never_chosen_at_ingest(client):
    await _post(
        client,
        _ev("a", occurred_at=_ts(1), complexity=5, complexity_method="ai-v0"),
        _ev("b", occurred_at=_ts(2), complexity=4),  # no method at all
        _ev("c", occurred_at=_ts(3), complexity=2, tags={"complexity_version": "request-shape-v1"}),
    )
    (task,) = await _tasks()
    assert (task["start_complexity"], task["start_complexity_method"]) == (2, "request-shape-v1")


# --- AC10a / AC10d -------------------------------------------------------------------


def _session_marker(cid, reason, second):
    return {"client_event_id": cid, "model": "m", "session_id": "s1", "event_type": "session",
            "finish_reason": reason, "occurred_at": _ts(second), "turn_id": "gw"}


async def _completion():
    return {t["turn_id"]: (t["completion"], t["completed_at"]) for t in await _tasks()}


EXPECTED = {
    "s1:t:1": ("next_task", _ts(10)),
    "s1:t:2": ("next_task", _ts(20)),
    "s1:t:3": ("session_end", _ts(40)),
}
SESSION_EVENTS = [
    _ev("a", turn="s1:t:1", occurred_at=_ts(1)),
    _ev("b", turn="s1:t:2", occurred_at=_ts(10)),
    _ev("c", turn="s1:t:3", occurred_at=_ts(20)),
    _session_marker("m", "end", 40),
    _session_marker("n", "start", 45),
]


async def test_completion_next_task_and_session_end(client):
    await _post(client, *SESSION_EVENTS)
    assert await _completion() == EXPECTED


async def test_completion_is_independent_of_arrival_order(client):
    await _post(client, *reversed(SESSION_EVENTS))  # marker and later tasks first
    assert await _completion() == EXPECTED


async def test_hierarchy_child_from_parent_turn_and_never_downgraded(client):
    await _post(client, _ev("p", turn="s1:t:1", task_hierarchy="root"))
    parent = task_store.task_ref("hermes", "s1", "s1:t:1")
    child = _ev("c1", turn="c1:t:1", session_id="c1", parent_session_id="s1", parent_turn_id="s1:t:1")
    await _post(client, child, _ev("c2", turn="c1:t:1", session_id="c1", task_hierarchy="root"))
    tasks = {t["turn_id"]: t for t in await _tasks()}
    assert tasks["s1:t:1"]["hierarchy_status"] == "root"
    assert (tasks["c1:t:1"]["hierarchy_status"], tasks["c1:t:1"]["parent_task_ref"]) == ("child", parent)
    assert tasks["c1:t:1"]["root_task_ref"] == parent
    grandchild = _ev("g", turn="g:t:1", session_id="g", parent_session_id="c1", parent_turn_id="c1:t:1")
    await _post(client, grandchild)
    tasks = {t["turn_id"]: t for t in await _tasks()}
    assert tasks["g:t:1"]["root_task_ref"] == parent


# --- AC5b / AC11c: prompt capture -------------------------------------------------------


async def test_capture_stores_scrubbed_prompt_in_tasks_only(client, monkeypatch):
    headers = _enable_capture(monkeypatch)
    now = datetime.now(timezone.utc)
    captured = (now - timedelta(minutes=1)).isoformat()
    secret = "sk-" + "a" * 24
    await _post(client, _ev("a", task_prompt_text=f"fix it with {secret}", task_prompt_captured_at=captured),
                headers=headers)
    (task,) = await _tasks()
    assert secret not in task["prompt_text"] and "[REDACTED:secret]" in task["prompt_text"]
    assert task["prompt_redaction_version"] == "task-redact-v1"
    assert task["prompt_hash"] == hashlib.sha256(task["prompt_text"].encode()).hexdigest()
    expires = datetime.fromisoformat(task["prompt_expires_at"].replace("Z", "+00:00"))
    stored_capture = datetime.fromisoformat(task["prompt_captured_at"].replace("Z", "+00:00"))
    assert expires - stored_capture == timedelta(days=30)
    async with AsyncSessionLocal() as session:
        leaked = (await session.execute(text(
            "SELECT COUNT(*) FROM token_events WHERE prompt_text IS NOT NULL OR prompt_hash IS NOT NULL"))).scalar()
    assert leaked == 0

    await _post(client, _ev("b", task_prompt_text="second prompt", task_prompt_captured_at=captured), headers=headers)
    (task2,) = await _tasks()
    assert task2["prompt_text"] == task["prompt_text"]  # first write wins
    features.refresh()


async def test_purged_prompt_is_never_repopulated(client, monkeypatch):
    headers = _enable_capture(monkeypatch)
    captured = datetime.now(timezone.utc).isoformat()
    await _post(client, _ev("a", task_prompt_text="one", task_prompt_captured_at=captured), headers=headers)
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE tasks SET prompt_text = NULL, prompt_purged_at = '2026-09-27T00:00:00Z'"))
        await session.commit()
    await _post(client, _ev("b", task_prompt_text="two", task_prompt_captured_at=captured), headers=headers)
    (task,) = await _tasks()
    assert task["prompt_text"] is None
    features.refresh()


async def test_old_capture_is_discarded_and_future_capture_is_clamped(client, monkeypatch):
    headers = _enable_capture(monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(days=30, minutes=1)).isoformat()
    await _post(client, _ev("a", turn="s1:t:1", task_prompt_text="old", task_prompt_captured_at=old), headers=headers)
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    await _post(client, _ev("b", turn="s1:t:2", occurred_at=_ts(2), task_prompt_text="new",
                            task_prompt_captured_at=future), headers=headers)
    tasks = {t["turn_id"]: t for t in await _tasks()}
    assert tasks["s1:t:1"]["prompt_text"] is None and tasks["s1:t:1"]["prompt_captured_at"] is None
    captured = datetime.fromisoformat(tasks["s1:t:2"]["prompt_captured_at"].replace("Z", "+00:00"))
    assert captured <= datetime.now(timezone.utc)
    features.refresh()


async def test_long_prompt_is_truncated_with_original_length(client, monkeypatch):
    headers = _enable_capture(monkeypatch)
    await _post(client, _ev("a", task_prompt_text="x" * 40_000,
                            task_prompt_captured_at=datetime.now(timezone.utc).isoformat()), headers=headers)
    (task,) = await _tasks()
    assert (len(task["prompt_text"]), task["prompt_length"], task["prompt_truncated"]) == (32_000, 40_000, 1)
    features.refresh()


async def test_capture_flag_without_token_discards_prompt(client, monkeypatch):
    monkeypatch.setenv("STORE_TASK_PROMPTS", "1")
    monkeypatch.delenv("INGEST_TOKEN", raising=False)
    features.refresh()
    await _post(client, _ev("a", task_prompt_text="secret plan",
                            task_prompt_captured_at=datetime.now(timezone.utc).isoformat()))
    (task,) = await _tasks()
    assert task["prompt_text"] is None
    assert (await client.get("/api/meta")).json()["task_prompt_capture_disabled_reason"] == "ingest_token_missing"
    features.refresh()


async def test_capture_disabled_by_default_discards_prompt(client):
    await _post(client, _ev("a", task_prompt_text="secret plan"))
    (task,) = await _tasks()
    assert task["prompt_text"] is None


# --- request composition ------------------------------------------------------------------


async def test_request_composition_counts_are_stored(client):
    await _post(client, _ev("a", request_system_chars=10, request_history_chars=20, request_tool_output_chars=30,
                            request_file_content_chars=40, request_file_ref_count=2,
                            request_tool_names=["read_file", "search_files", "read_file"]))
    async with AsyncSessionLocal() as session:
        row = (await session.execute(text(
            "SELECT request_system_chars, request_history_chars, request_tool_output_chars, "
            "request_file_content_chars, request_file_ref_count, request_tool_names_json FROM token_events"))).one()
    assert tuple(row) == (10, 20, 30, 40, 2, '["read_file","search_files"]')


async def test_invalid_tool_name_is_rejected(client):
    response = await client.post("/api/events", json=_ev("a", request_tool_names=["bad name!"]), headers=H)
    assert response.status_code == 422
    batch = await client.post("/api/events/batch", json={"events": [_ev("b", request_tool_names=["x y"])]}, headers=H)
    assert batch.json()["rejected"] == 1
