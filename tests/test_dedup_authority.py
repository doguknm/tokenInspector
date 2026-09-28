"""Dedup authority and the Claude Code producer against the real app (O9 C10, C6, C8; AC2.4, AC2.5, AC2.7, AC2.10)."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import task_store
from cc_support import Transcript, asgi_urlopen, make_repo, modules, set_home, stdin_bytes, subagent_path
from database import AsyncSessionLocal
from routes.events import _normalize_reserved

JOB = "20260928-101010-4242"


@pytest.fixture
def cc(tmp_path, monkeypatch):
    set_home(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("TOKEN_INSPECTOR_URL", "http://test")
    mods = modules()
    monkeypatch.setattr(mods["cc_hook"], "HOOK_BOUND_S", 60.0)  # in-process: never kill pytest
    monkeypatch.setattr(mods["cc_hook"], "SEND_DEADLINE_S", 30.0)
    return mods


async def hook(client, cc, monkeypatch, event, transcript, cwd, **kw):
    loop = asyncio.get_running_loop()
    acks = []
    real_post = cc["cc_client"].post_batch

    def recording_post(*a, **k):
        ack = real_post(*a, **k)
        acks.append(ack)
        return ack

    monkeypatch.setattr(cc["cc_client"].urllib.request, "urlopen", asgi_urlopen(client, loop))
    monkeypatch.setattr(cc["cc_client"], "post_batch", recording_post)
    await asyncio.to_thread(cc["cc_hook"].main, io.BytesIO(stdin_bytes(event, transcript, cwd, **kw)))
    return acks


async def rows(sql, **params):
    async with AsyncSessionLocal() as session:
        return [tuple(r) for r in (await session.execute(text(sql), params)).all()]


def recent(minutes=1):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --- AC2.10 pairing -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("tags", "expected"), [
    ({"runtime": "claude-code@windows", "producer": "hermes-plugin"}, {"attribution_invalid": True}),
    ({"runtime": "hermes-agent", "producer": "claude-code-hook"}, {"attribution_invalid": True}),
    ({"runtime": "claude-code@windows", "producer": "claude-code-hook"},
     {"runtime": "claude-code@windows", "producer": "claude-code-hook"}),
    ({"runtime": "claude-code@hermes", "producer": "claude-code-hook"},
     {"runtime": "claude-code@hermes", "producer": "claude-code-hook"}),
    ({"runtime": "hermes-agent", "producer": "hermes-plugin"}, {"runtime": "hermes-agent", "producer": "hermes-plugin"}),
    ({"runtime": "app", "producer": "app-provider"}, {"runtime": "app", "producer": "app-provider"}),
    ({"runtime": "claude-code@hermes"}, {"attribution_invalid": True}),  # missing producer
    ({"producer": "claude-code-hook"}, {"attribution_invalid": True}),  # missing runtime
    ({"runtime": "claude-code@hermes", "producer": "zz-bogus"}, {"attribution_invalid": True}),
    ({}, {}),  # genuine legacy: no runtime, no producer, no mark
])
async def test_runtime_producer_pairing(client, tags, expected):
    assert _normalize_reserved(tags) == expected
    body = {"client_event_id": "pair-1", "model": "claude-sonnet-4-6", "prompt_tokens": 1, "tags": tags}
    response = await client.post("/api/events/batch", json={"events": [body]}, headers={"X-Project-Name": "p"})
    assert response.json()["inserted"] == 1  # never rejected
    stored = await rows("SELECT tags_json FROM token_events WHERE client_event_id = 'pair-1'")
    assert json.loads(stored[0][0] or "{}") == expected


async def test_invalid_pair_not_counted_end_to_end(client):
    def ev(cid, session, tags):
        return {"client_event_id": cid, "model": "claude-sonnet-4-6", "session_id": session, "turn_id": "t",
                "prompt_tokens": 10, "occurred_at": recent(), "tags": dict(tags, job_ref=JOB)}
    events = [
        ev("marked", "s1", {"runtime": "claude-code@windows", "producer": "hermes-plugin"}),
        ev("legacy", "s2", {}),
        ev("cc-" + "a" * 32, "s3", {"runtime": "claude-code@windows", "producer": "claude-code-hook"}),
    ]
    response = await client.post("/api/events/batch", json={"events": events}, headers={"X-Project-Name": "p"})
    assert response.json()["inserted"] == 3
    data = (await client.get("/api/jobs", params={"days": 1})).json()
    (job,) = [j for j in data["items"] if j["job_ref"] == JOB]
    assert job["llm_request_count"] == 2 and job["runtimes"] == ["claude-code@windows"]
    assert data["anomalies"]["invalid_attribution_events"] == 1


async def test_no_event_from_hook_stdin(client, cc, monkeypatch, tmp_path):
    t = Transcript(tmp_path / "proj" / "empty.jsonl", "empty")
    t.prompt()
    t.flush()
    stdin = json.loads(stdin_bytes("Stop", t.path, str(tmp_path)))
    stdin.update({"usage": {"input_tokens": 999, "output_tokens": 999}, "message": {"id": "m", "usage": {}},
                  "model": "claude-sonnet-4-6"})
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(cc["cc_client"].urllib.request, "urlopen", asgi_urlopen(client, loop))
    await asyncio.to_thread(cc["cc_hook"].main, io.BytesIO(json.dumps(stdin).encode()))
    assert await rows("SELECT COUNT(*) FROM token_events") == [(0,)]


# --- AC2.4 dedup ---------------------------------------------------------------------------------------------


async def test_cross_project_cc_dedup(client, cc, monkeypatch, tmp_path):
    repo = make_repo(tmp_path / "RepoA")
    t = Transcript(tmp_path / "proj" / "sess.jsonl", "sess", cwd=str(repo))
    t.prompt()
    t.call("m1", "r1")
    t.assistant_line("m2", None, usage={"input_tokens": 1, "output_tokens": 1}, stop="end_turn")  # no requestId
    t.call("m3", "r3")
    t.flush()
    acks = await hook(client, cc, monkeypatch, "Stop", t.path, str(repo))
    assert [a["inserted"] for a in acks] == [3]
    # the same calls resolved to another project (alias change / resumed copy under another cwd)
    monkeypatch.setenv("TOKEN_INSPECTOR_PROJECT_ALIASES", json.dumps({str(repo): "project-b"}))
    copy = Transcript(tmp_path / "proj" / "resumed.jsonl", "resumed", cwd=str(repo))
    copy.prompt()
    copy.call("m1", "r1")
    copy.assistant_line("m2", None, usage={"input_tokens": 1, "output_tokens": 1}, stop="end_turn")
    copy.call("m3", "r3-changed")  # a changed requestId is a different call
    copy.flush()
    acks = await hook(client, cc, monkeypatch, "Stop", copy.path, str(repo))
    assert (acks[0]["inserted"], acks[0]["duplicates"]) == (1, 2)
    stored = await rows("SELECT project_name, COUNT(*) FROM token_events GROUP BY project_name ORDER BY 1")
    assert stored == [("project-b", 1), ("repoa", 3)]  # first arrival owns the project


async def test_rerun_and_resume_count_once(client, cc, monkeypatch, tmp_path):
    t = Transcript(tmp_path / "proj" / "sess.jsonl", "sess")
    t.prompt()
    for i in range(4):
        t.call(f"m{i}", f"r{i}", lines=2)
    t.flush()
    await hook(client, cc, monkeypatch, "Stop", t.path, str(tmp_path))
    # rerun with the cursor deleted
    for path in cc["cc_config"].state_dir().glob("*.json"):
        if path.name != "counters.json":
            path.unlink()
    acks = await hook(client, cc, monkeypatch, "Stop", t.path, str(tmp_path))
    assert acks[0]["duplicates"] == 4 and acks[0]["inserted"] == 0
    # a resumed session copies earlier messages (same message.id + requestId) into its own file
    resumed = Transcript(tmp_path / "proj" / "resumed.jsonl", "resumed")
    resumed.prompt()
    for i in range(4):
        resumed.call(f"m{i}", f"r{i}", lines=2)
    resumed.prompt()
    resumed.call("new", "rnew")
    resumed.flush()
    acks = await hook(client, cc, monkeypatch, "Stop", resumed.path, str(tmp_path))
    assert (acks[0]["inserted"], acks[0]["duplicates"]) == (1, 4)
    assert await rows("SELECT COUNT(*) FROM token_events") == [(5,)]


# --- AC2.5 children ------------------------------------------------------------------------------------------


async def test_subagent_is_child_task(client, cc, monkeypatch, tmp_path):
    ce = cc["cc_events"]
    repo = make_repo(tmp_path / "ChildRepo")
    main = tmp_path / "proj" / "sess-main.jsonl"
    parent = Transcript(main, "sess-main", cwd=str(repo))
    pid = parent.prompt()
    parent.call("pm1", "pr1", tool_uses=1)
    parent.flush()
    sub = Transcript(subagent_path(main, "abc0123456789def0"), "sess-main", agent_id="abc0123456789def0")
    sub.prompt(pid)
    sub.call("sm1", "sr1")
    sub.call("sm2", "sr2")
    sub.flush()
    await hook(client, cc, monkeypatch, "SubagentStop", main, str(repo), agent_transcript=sub.path)
    child_session = ce.pseudonym("sess-main\x1fabc0123456789def0")
    events = await rows("SELECT session_id, turn_id, role FROM token_events ORDER BY session_id")
    assert sorted(events) == sorted([(ce.pseudonym("sess-main"), ce.pseudonym(pid), "primary"),
                                     (child_session, child_session, "subagent"),
                                     (child_session, child_session, "subagent")])
    parent_ref = task_store.task_ref("childrepo", ce.pseudonym("sess-main"), ce.pseudonym(pid))
    tasks = dict(((r[0], r[1]), r[2:]) for r in await rows(
        "SELECT session_id, turn_id, hierarchy_status, parent_task_ref, root_task_ref FROM tasks"))
    assert tasks[(child_session, child_session)] == ("child", parent_ref, parent_ref)
    assert tasks[(ce.pseudonym("sess-main"), ce.pseudonym(pid))][0] == "root"


# --- AC2.7 contract with the real app -----------------------------------------------------------------------


async def test_producer_events_never_rejected_by_backend(client, cc, monkeypatch, tmp_path):
    main = tmp_path / "proj" / "edge.jsonl"
    t = Transcript(main, "edge")
    pid = t.prompt()
    t.call(input_tokens=0, output=0)
    t.call(input_tokens=10**12, output=10**9, cache_read=10**12, cache_create=10**11)
    t.assistant_line("nocache", "r", usage={"input_tokens": 1, "output_tokens": 1}, stop="end_turn")
    t.call(final=False, extra={"isAbortedMidStream": True})
    t.call(stop="weird Stop!")
    t.call(model="zz-canary-host.internal")
    t.call(tool_uses=3, lines=3)
    t.flush()
    sub = Transcript(subagent_path(main, "e1"), "edge", agent_id="e1")
    sub.prompt(pid)
    sub.call()
    sub.flush()
    acks = await hook(client, cc, monkeypatch, "SubagentStop", main, str(tmp_path), agent_transcript=sub.path)
    assert sum(a["rejected"] for a in acks) == 0
    assert sum(a["inserted"] for a in acks) == 8
    unknown = await rows("SELECT cost_status FROM token_events WHERE model = 'unknown'")
    assert unknown and unknown[0][0] != "priced"


async def test_token_accepted_when_ingest_token_set(client, cc, monkeypatch, tmp_path):
    monkeypatch.setenv("INGEST_TOKEN", "tok-" + "z" * 20)
    t = Transcript(tmp_path / "proj" / "auth.jsonl", "auth")
    t.prompt()
    t.call()
    t.flush()
    acks = await hook(client, cc, monkeypatch, "Stop", t.path, str(tmp_path))
    assert acks == [None]  # 401 without a token: fail-open, no advance
    monkeypatch.setenv("TOKEN_INSPECTOR_API_KEY", "tok-" + "z" * 20)
    acks = await hook(client, cc, monkeypatch, "Stop", t.path, str(tmp_path))
    assert acks[0]["inserted"] == 1
