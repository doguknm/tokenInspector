"""O9 Phase 1: reserved job tag normalization, tag cap and one job per task (J1, J2, J4)."""

import json
from pathlib import Path

import pytest
from sqlalchemy import text

import task_store
from database import AsyncSessionLocal
from routes import events as events_module
from routes.events import TAG_BYTES_MAX, _clean_tags, _normalize_reserved

H = {"X-Project-Name": "hermes"}
FIXTURES = Path(__file__).parent / "fixtures"
PLUGIN_FIXTURE = Path(__file__).resolve().parents[2] / "token_inspector" / "tests" / "fixtures" / "worst_case_plugin_tags.json"
JOB_A, JOB_B, JOB_C = "20260928-100000-111", "20260928-110000-222", "devir-20260928-120000-333"
PLUGIN = {"runtime": "hermes-agent", "producer": "hermes-plugin"}


def event(cid, *, session="s1", turn="t1", job=None, tags=None, model="claude-sonnet-4-6", at=None, **extra):
    body_tags = dict(PLUGIN)
    if job is not None:
        body_tags["job_ref"] = job
    body_tags.update(tags or {})
    body = {"client_event_id": cid, "model": model, "session_id": session, "turn_id": turn,
            "prompt_tokens": 10, "completion_tokens": 5, "tags": body_tags,
            "occurred_at": at or "2026-09-28T10:00:00Z"}
    body.update(extra)
    return body


async def post(client, *bodies, project="hermes"):
    response = await client.post("/api/events/batch", json={"events": list(bodies)}, headers={"X-Project-Name": project})
    assert response.status_code == 200, response.text
    return response.json()


async def rows(sql, **params):
    async with AsyncSessionLocal() as session:
        return [tuple(r) for r in (await session.execute(text(sql), params)).all()]


# --- AC1.3 ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["brainstorm", "review", "code", "devir", "k1", "k2", "other"])
def test_reserved_keys_normalization_keeps_known_work_types(value):
    assert _normalize_reserved({"work_type": value}) == {"work_type": value}


@pytest.mark.parametrize(("tags", "expected"), [
    ({"work_type": "REVIEW "}, {"work_type": "review"}),
    ({"work_type": "brainstorming"}, {"work_type": "other"}),
    ({"work_type": 7}, {"work_type": "other"}),
    ({"work_type": True}, {"work_type": "other"}),
    ({"work_type": None}, {}),
    ({"work_type": ""}, {}),
    ({}, {}),
    ({"job_ref": "../etc/passwd"}, {}),
    ({"job_ref": "sk-ant-CANARYSECRET0123"}, {}),
    ({"job_ref": 20260928}, {}),
    ({"job_ref": JOB_A}, {"job_ref": JOB_A}),
    ({"job_ref": JOB_C}, {"job_ref": JOB_C}),
    ({"job_attempt": 0}, {}),
    ({"job_attempt": -1}, {}),
    ({"job_attempt": True}, {}),
    ({"job_attempt": 1.0}, {}),
    ({"job_attempt": "2"}, {}),
    ({"job_attempt": 3}, {"job_attempt": 3}),
    ({"runtime": "hermes-agent", "producer": "hermes-plugin"}, {"runtime": "hermes-agent", "producer": "hermes-plugin"}),
    ({"runtime": "zz-canary-host.internal"}, {"attribution_invalid": True}),
    ({"producer": "other-producer", "runtime": "hermes-agent"}, {"runtime": "hermes-agent", "attribution_invalid": True}),
    ({"runtime": ["hermes-agent"]}, {"attribution_invalid": True}),
    ({"attribution_invalid": True, "runtime": "app"}, {"runtime": "app"}),
    ({"attribution_invalid": False}, {}),
    ({"api_mode": "chat_completions", "schema": "x"}, {"api_mode": "chat_completions", "schema": "x"}),
])
def test_reserved_keys_normalization(tags, expected):
    assert _normalize_reserved(tags) == expected


def test_normalization_never_adds_keys():
    tags = {f"k{i}": i for i in range(19)} | {"runtime": "bogus"}
    out = _normalize_reserved(tags)
    assert len(out) == 20 and out["attribution_invalid"] is True and "runtime" not in out
    assert _clean_tags(out)  # still within the 20-key limit


INVALID_RESERVED = [
    {"job_ref": "C:\\Users\\ZZ-CANARY-USER\\s"}, {"job_ref": 5}, {"work_type": {"a": 1}}, {"work_type": "Bad Value"},
    {"job_attempt": "x"}, {"job_attempt": 0}, {"runtime": "bogus"}, {"producer": 1}, {"attribution_invalid": "yes"},
]


async def test_reserved_keys_never_reject(client):
    for n, tags in enumerate(INVALID_RESERVED):
        single = await client.post("/api/events", json=event(f"single-{n}", turn=f"s{n}", tags=tags), headers=H)
        assert single.status_code == 201, single.text
    ack = await post(client, *[event(f"batch-{n}", turn=f"b{n}", tags=tags) for n, tags in enumerate(INVALID_RESERVED)])
    assert (ack["inserted"], ack["rejected"]) == (len(INVALID_RESERVED), 0)
    stored = dict(await rows("SELECT client_event_id, tags_json FROM token_events"))
    for n, tags in enumerate(INVALID_RESERVED):
        for prefix in ("single", "batch"):
            assert json.loads(stored[f"{prefix}-{n}"]) == _normalize_reserved(PLUGIN | tags)


async def test_job_attempt_separate_from_attempt(client):
    await post(client, event("e1", job=JOB_A, tags={"job_attempt": 1}, attempt=3))
    ((attempt, tags),) = await rows("SELECT attempt, tags_json FROM token_events")
    assert attempt == 3 and json.loads(tags)["job_attempt"] == 1


# --- AC1.4 ---------------------------------------------------------------------------------------------------


def _fixture_tags():
    return json.loads((FIXTURES / "worst_case_plugin_tags.json").read_text(encoding="utf-8"))


async def test_worst_case_plugin_tags_accepted(client):
    tags = _fixture_tags()
    assert len(tags) == 17
    ack = await post(client, {"client_event_id": "w1", "model": "m", "tags": tags})
    assert (ack["inserted"], ack["rejected"]) == (1, 0)
    # the cap itself: exactly TAG_BYTES_MAX bytes is accepted, one byte over is still rejected
    ack = await post(client, {"client_event_id": "w2", "model": "m", "tags": _tags_of_size(TAG_BYTES_MAX)},
                     {"client_event_id": "w3", "model": "m", "tags": _tags_of_size(TAG_BYTES_MAX + 1)})
    assert (ack["inserted"], ack["rejected"]) == (1, 1) and ack["items"][1]["rejected"] is True


def _tags_of_size(target: int) -> dict:
    tags, i = {}, 0
    while True:
        tags[f"k{i:02d}"] = ""
        room = target - len(json.dumps(tags, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        tags[f"k{i:02d}"] = "x" * min(128, room)
        if room <= 128:
            break
        i += 1
    assert len(json.dumps(tags, separators=(",", ":"), sort_keys=True).encode("utf-8")) == target and len(tags) <= 20
    return tags


def test_tag_cap_is_the_constant():
    assert TAG_BYTES_MAX == 1024 and events_module.TAG_BYTES_MAX == TAG_BYTES_MAX


def test_fixture_files_identical():
    if not PLUGIN_FIXTURE.is_file():
        pytest.skip("plugin repo not checked out next to the backend")
    ours = (FIXTURES / "worst_case_plugin_tags.json").read_bytes().replace(b"\r\n", b"\n")
    assert PLUGIN_FIXTURE.read_bytes().replace(b"\r\n", b"\n") == ours


# --- AC1.5 (task -> job) -------------------------------------------------------------------------------------


async def _task(turn="t1", project="hermes", session="s1"):
    (row,) = await rows("SELECT job_ref, job_ref_conflicts FROM tasks WHERE id = :id",
                        id=task_store.task_ref(project, session, turn))
    return row


async def test_task_gets_first_job(client):
    await post(client, event("e1", job=JOB_A, at="2026-09-28T10:00:00Z"))
    await post(client, event("e2", job=JOB_B, at="2026-09-28T10:00:01Z"))
    assert await _task() == (JOB_A, 1)
    await post(client, event("e3", job=JOB_A, at="2026-09-28T10:00:02Z"))
    assert await _task() == (JOB_A, 1)
    await post(client, event("e4", at="2026-09-28T10:00:03Z"))
    assert await _task() == (JOB_A, 1)


async def test_task_without_job_then_job(client):
    await post(client, event("e1"))
    assert await _task() == (None, 0)
    await post(client, event("e2", job=JOB_B))
    assert await _task() == (JOB_B, 0)


async def test_duplicate_delivery_does_not_count_conflict(client):
    await post(client, event("e1", job=JOB_A), event("e2", job=JOB_B))
    assert await _task() == (JOB_A, 1)
    ack = await post(client, event("e2", job=JOB_B))
    assert ack["duplicates"] == 1
    assert await _task() == (JOB_A, 1)


async def test_invalid_job_ref_never_assigned(client):
    await post(client, event("e1", job="not-a-job"), event("e2", job=JOB_A))
    assert await _task() == (JOB_A, 0)
