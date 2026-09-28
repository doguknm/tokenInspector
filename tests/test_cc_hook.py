"""Claude Code hook: sending, privacy, fail-open, bound, checkpoints, drain (O9 C1-C8; AC2.2-AC2.7)."""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import time
import types
from pathlib import Path

import pytest

from cc_support import (CANARY_HOST, CANARY_PATH_POSIX, CANARY_PATH_WIN, FakeServer, Transcript, canaries,
                        make_repo, modules, run_hook_subprocess, set_home, stdin_bytes, subagent_path)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    set_home(monkeypatch, h)
    return h


@pytest.fixture
def cc(home):
    return modules()


@pytest.fixture
def server(monkeypatch):
    s = FakeServer()
    monkeypatch.setenv("TOKEN_INSPECTOR_URL", s.url)
    yield s
    s.close()


def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Doğukan Mutlu" / ".claude" / "projects" / "C--work-proj"  # non-ASCII profile path
    d.mkdir(parents=True, exist_ok=True)
    return d


def session(tmp_path: Path, sid: str = "11111111-1111-1111-1111-111111111111", cwd: str = CANARY_PATH_WIN) -> Transcript:
    return Transcript(project_dir(tmp_path) / f"{sid}.jsonl", sid, cwd=cwd)


def run(cc, event: str, transcript: Path, cwd: str, **kw) -> None:
    assert cc["cc_hook"].main(io.BytesIO(stdin_bytes(event, transcript, cwd, **kw))) == 0


def state(cc, path: Path):
    return cc["cc_state"].load(cc["cc_config"].state_dir(), cc["cc_state"].key_for(str(path)))


def counters(cc) -> dict:
    path = cc["cc_config"].state_dir() / "counters.json"
    return json.loads(path.read_bytes()) if path.exists() else {}


def sub_env(home: Path, url: str, **extra) -> dict:
    env = {"HOME": str(home), "USERPROFILE": str(home), "LOCALAPPDATA": str(home / "AppData" / "Local"),
           "TOKEN_INSPECTOR_URL": url, "TI_CC_TEST_MODE": "1"}
    for name in ("TOKEN_INSPECTOR_API_KEY", "TOKEN_INSPECTOR_PROJECT_ALIASES", "TOKEN_INSPECTOR_JOB_REF",
                 "TOKEN_INSPECTOR_WORK_TYPE", "TOKEN_INSPECTOR_JOB_ATTEMPT"):
        env[name] = ""
    env.update(extra)
    return env


# --- AC2.2 / AC2.3 ------------------------------------------------------------------------------------------


def test_stop_hook_sends_turn_calls(cc, server, tmp_path):
    repo = make_repo(tmp_path / "work" / "MyRepo", "git@github.com:Org/MyRepo.git")
    t = session(tmp_path, cwd=str(repo))
    t.prompt()
    t.call(lines=2, input_tokens=100, cache_read=5000, cache_create=300, output=42, early_output=1,
           tool_result_between=True, tool_uses=1)
    t.call(output=7)
    t.prompt()
    t.call(output=9)
    t.flush()
    run(cc, "Stop", t.path, str(repo))
    events = server.events()
    assert len(events) == 3
    expected_runtime = "claude-code@windows" if sys.platform == "win32" else "claude-code@hermes"
    assert {e["tags"]["runtime"] for e in events} == {expected_runtime}
    assert {e["tags"]["producer"] for e in events} == {"claude-code-hook"}
    assert all(e["event_type"] == "llm_request" and e["model"] == "claude-sonnet-4-6" for e in events)
    assert (events[0]["prompt_tokens"], events[0]["cache_read_tokens"], events[0]["cache_creation_tokens"],
            events[0]["completion_tokens"]) == (100, 5000, 300, 42)
    assert len({e["turn_id"] for e in events}) == 2  # one task per user turn
    assert server.headers[0]["X-Project-Name"] == "myrepo"
    assert events[0]["tags"]["project_source"] == "git_remote"


def test_other_platform_sends_nothing(cc, server, tmp_path, monkeypatch):
    monkeypatch.setattr(cc["cc_config"], "sys", types.SimpleNamespace(platform="darwin"))
    t = session(tmp_path)
    t.prompt()
    t.call()
    t.flush()
    run(cc, "Stop", t.path, str(tmp_path))
    assert server.batches == 0


def test_devir_run_tags(cc, server, tmp_path, monkeypatch):
    monkeypatch.setattr(cc["cc_config"], "sys", types.SimpleNamespace(platform="linux"))
    monkeypatch.setenv("TOKEN_INSPECTOR_JOB_REF", "devir-20260928-101010-4242")
    monkeypatch.setenv("TOKEN_INSPECTOR_WORK_TYPE", "devir")
    clone = make_repo(tmp_path / "PEGADocRagAgent-devir", "git@github.com:someone/PEGADocRag.git")
    t = session(tmp_path, cwd=str(clone))
    t.prompt()
    t.call()
    t.flush()
    run(cc, "Stop", t.path, str(clone))
    (event,) = server.events()
    assert event["tags"]["runtime"] == "claude-code@hermes"
    assert event["tags"]["work_type"] == "devir" and event["tags"]["job_ref"] == "devir-20260928-101010-4242"
    assert server.headers[0]["X-Project-Name"] == "pegadocrag"
    for key in ("prompt_text", "task_prompt_text", "prompt_hash", "prompt_length"):
        assert key not in event


# --- AC2.6 --------------------------------------------------------------------------------------------------


def test_payload_sentinel_bytes(cc, server, tmp_path):
    ce = cc["cc_events"]
    base = tmp_path / "ZZ-CANARY-USER" / "secret"
    main = base / "sess-canary.jsonl"
    t = Transcript(main, "sess-canary", cwd=CANARY_PATH_POSIX)
    pid = t.prompt()
    t.call(tool_uses=2, lines=2, extra={"slug": "zz-canary-agent", "attributionSkill": "zz-canary-agent"})
    t.flush()
    sub = Transcript(subagent_path(main, "agentzz"), "sess-canary", agent_id="agentzz", cwd=CANARY_PATH_WIN)
    sub.prompt(pid)
    sub.call()
    sub.flush()
    run(cc, "SubagentStop", main, CANARY_PATH_WIN, agent_transcript=sub.path)
    assert len(server.events()) == 2
    for body in server.bodies:
        assert not any(c in body for c in canaries())
    for header in server.headers:
        assert not any(c.decode() in header.get("X-Project-Name", "") for c in canaries())
    for event in server.events():
        assert set(event) <= ce.ALLOWED_FIELDS and set(event["tags"]) <= ce.TAG_KEYS
        assert "request_tool_names" not in event and "tool_call_count" in event
    root = next(e for e in server.events() if e["task_hierarchy"] == "root")
    assert root["tool_call_count"] == 2


def test_payload_value_canaries(cc, server, tmp_path, monkeypatch):
    ce = cc["cc_events"]
    host = socket.gethostname()
    repo = make_repo(tmp_path / "10.9.8.7")  # IPv4-shaped repo dir, no origin
    monkeypatch.setenv("TOKEN_INSPECTOR_PROJECT_ALIASES", json.dumps({str(repo): host.lower()[:60] or "x"}))
    monkeypatch.setenv("TOKEN_INSPECTOR_JOB_REF", "sk-ant-CANARYSECRET0123")
    raw_session = CANARY_PATH_WIN + "\\s"
    main = project_dir(tmp_path) / "value.jsonl"
    t = Transcript(main, raw_session, cwd=str(repo))
    pid = t.prompt("uuid-" + CANARY_PATH_POSIX)
    t.call(model=CANARY_HOST)
    t.flush()
    sub = Transcript(subagent_path(main, "agent" + "zz"), raw_session, agent_id="C:/zz-canary/agent")
    sub.prompt(pid)
    sub.call()
    sub.flush()
    run(cc, "SubagentStop", main, str(repo), agent_transcript=sub.path)
    events = {e["task_hierarchy"]: e for e in server.events()}
    root, child = events["root"], events["child"]
    assert root["model"] == "unknown"
    assert root["session_id"] == ce.pseudonym(raw_session) and root["turn_id"] == ce.pseudonym(pid)
    assert child["parent_session_id"] == root["session_id"] and child["parent_turn_id"] == root["turn_id"]
    assert all(len(e[k]) == 32 and all(ch in "0123456789abcdef" for ch in e[k])
               for e in (root, child) for k in ("session_id", "turn_id"))
    assert "job_ref" not in root["tags"]
    assert {h["X-Project-Name"] for h in server.headers} == {"claude-code"}  # hostname alias + IPv4 dir fall through
    assert root["tags"]["project_source"] == "fallback"
    for body in server.bodies:
        assert not any(c in body for c in canaries()) and b"sk-ant" not in body


# --- AC2.7: fail-open, bound, silence ----------------------------------------------------------------------


FAILURES = ["down", "status:401", "status:422", "status:500", "garbage", "malformed_line", "missing_transcript",
            "invalid_stdin", "non_utf8_stdin"]


@pytest.mark.parametrize("failure", FAILURES)
def test_hook_exits_zero_on_every_failure(home, tmp_path, failure):
    srv = FakeServer(failure if failure.startswith(("status:", "garbage")) else "accept")
    try:
        url = "http://127.0.0.1:9" if failure == "down" else srv.url
        t = session(tmp_path)
        t.prompt()
        if failure == "malformed_line":
            t.raw(b"{broken json\n").raw(b"\xff\xfe\n")
        t.call()
        t.flush()
        path = t.path if failure != "missing_transcript" else t.path.with_name("absent.jsonl")
        stdin = stdin_bytes("Stop", path, str(tmp_path))
        if failure == "invalid_stdin":
            stdin = b"{not json"
        elif failure == "non_utf8_stdin":
            stdin = b"\xff\xfe\x00garbage"
        code, out, err, _ = run_hook_subprocess(stdin, sub_env(home, url))
        assert (code, out, err) == (0, b"", b"")
    finally:
        srv.close()


def test_hook_silent_on_success(home, tmp_path):
    srv = FakeServer()
    try:
        t = session(tmp_path)
        t.prompt()
        t.call()
        t.call()
        t.flush()
        code, out, err, _ = run_hook_subprocess(stdin_bytes("Stop", t.path, str(tmp_path)), sub_env(home, srv.url))
        assert (code, out, err) == (0, b"", b"")
        assert len(srv.events()) == 2
    finally:
        srv.close()


def test_hook_bounded_on_hanging_backend(home, tmp_path):
    srv = FakeServer("hang")
    try:
        t = session(tmp_path)
        t.prompt()
        t.call()
        t.flush()
        env = sub_env(home, srv.url, TI_CC_HOOK_BOUND_S="3", TI_CC_SEND_DEADLINE_S="2.5")
        code, out, err, elapsed = run_hook_subprocess(stdin_bytes("Stop", t.path, str(tmp_path)), env, timeout=20)
        assert code == 0 and out == b"" and err == b""
        assert elapsed < 3 + 1
    finally:
        srv.close()


def test_hook_bounded_on_blocked_stdin_and_slow_io(home, tmp_path):
    srv = FakeServer()
    try:
        # (a) stdin is a pipe that is never closed
        env = sub_env(home, srv.url, TI_CC_HOOK_BOUND_S="2")
        code, out, err, elapsed = run_hook_subprocess(None, env, timeout=20, keep_stdin_open=True)
        assert code == 0 and out == b"" and err == b"" and elapsed < 2 + 1
        # (b) slow transcript reads: the watchdog still ends the hook; the state file is never partial
        t = session(tmp_path)
        t.prompt()
        for _ in range(40):
            t.call()
        t.flush()
        env = sub_env(home, srv.url, TI_CC_HOOK_BOUND_S="2", TI_CC_READ_DELAY_S="0.2", TI_CC_SEND_DEADLINE_S="10")
        code, out, err, elapsed = run_hook_subprocess(stdin_bytes("Stop", t.path, str(tmp_path)), env, timeout=20)
        assert code == 0 and out == b"" and err == b"" and elapsed < 2 + 1
        state_dir = home / "AppData" / "Local" / "token_inspector_cc" if sys.platform == "win32" else \
            home / ".local" / "state" / "token_inspector_cc"
        for path in state_dir.glob("*.json"):
            json.loads(path.read_bytes())
    finally:
        srv.close()


# --- AC2.7: cursor and checkpoints -------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["status:401", "status:500", "garbage", "down"])
def test_cursor_advances_only_on_valid_ack(cc, tmp_path, monkeypatch, mode):
    srv = FakeServer(mode if mode != "down" else "accept")
    monkeypatch.setenv("TOKEN_INSPECTOR_URL", "http://127.0.0.1:9" if mode == "down" else srv.url)
    try:
        t = session(tmp_path)
        t.prompt()
        t.call()
        t.flush()
        run(cc, "Stop", t.path, str(tmp_path))
        st = state(cc, t.path)
        assert st is None or st["offset"] == 0
        assert counters(cc)["failed_posts"] == 1
        # recovery: a valid ack (with a reject) advances; a partial line at EOF is not consumed
        srv.mode = "accept"
        srv.rejected_next = 1
        monkeypatch.setenv("TOKEN_INSPECTOR_URL", srv.url)
        t.call()
        t.flush()
        complete = t.path.stat().st_size
        with open(t.path, "ab") as fh:
            fh.write(b'{"type":"assistant"')
        run(cc, "Stop", t.path, str(tmp_path))
        assert state(cc, t.path)["offset"] == complete
    finally:
        srv.close()


def test_checkpoint_per_batch_progress(cc, tmp_path, monkeypatch):
    """A-F4: batches 1-2 acked, batch 3 fails -> checkpoint after batch 2; later runs finish; each call once."""
    monkeypatch.setattr(cc["cc_client"], "BATCH_SIZE", 2)
    srv = FakeServer("fail_from:3")
    monkeypatch.setenv("TOKEN_INSPECTOR_URL", srv.url)
    try:
        t = session(tmp_path)
        t.prompt()
        ids = [t.call()[0] for _ in range(6)]
        t.flush()
        run(cc, "Stop", t.path, str(tmp_path))
        records = cc["cc_transcript"].read_window(str(t.path), 0, None).records
        assert state(cc, t.path)["offset"] == records[4].start_offset  # first group of batch 3
        assert len(srv.seen) == 4
        srv.mode = "accept"
        run(cc, "Stop", t.path, str(tmp_path))
        assert len(srv.seen) == 6 and state(cc, t.path)["offset"] == t.path.stat().st_size
        # 4 acked + failed batch 3 (2) + batch 3 again and batch 4 (4): earlier batches were never resent
        assert len(srv.events()) == 8
        assert len(ids) == 6
    finally:
        srv.close()


def test_checkpoint_survives_watchdog_kill_between_batches(home, tmp_path):
    srv = FakeServer("delay:0.8")
    try:
        t = session(tmp_path)
        t.prompt()
        for _ in range(8):
            t.call()
        t.flush()
        env = sub_env(home, srv.url, TI_CC_HOOK_BOUND_S="2", TI_CC_SEND_DEADLINE_S="30", TI_CC_BATCH_SIZE="1")
        code, *_ = run_hook_subprocess(stdin_bytes("Stop", t.path, str(tmp_path)), env, timeout=20)
        assert code == 0
        first = len(srv.seen)
        assert 1 <= first < 8
        srv.mode = "accept"
        state_dir = home / "AppData" / "Local" / "token_inspector_cc" if sys.platform == "win32" else             home / ".local" / "state" / "token_inspector_cc"
        locks = list(state_dir.glob("*.lock"))
        assert len(locks) == 1  # os._exit left the lock; it becomes stale after 2 x HOOK_BOUND_S
        os.utime(locks[0], (time.time() - 60, time.time() - 60))
        env = sub_env(home, srv.url, TI_CC_BATCH_SIZE="1")
        run_hook_subprocess(stdin_bytes("Stop", t.path, str(tmp_path)), env, timeout=20)
        assert len(srv.seen) == 8
        # resumed from the last checkpoint: only the call in flight at the kill may be resent
        assert len(srv.events()) <= 8 + 1
    finally:
        srv.close()


def test_rejected_items_dropped_and_counted(cc, server, tmp_path):
    server.rejected_next = 1
    t = session(tmp_path)
    t.prompt()
    for _ in range(5):
        t.call()
    t.flush()
    run(cc, "Stop", t.path, str(tmp_path))
    assert state(cc, t.path)["offset"] == t.path.stat().st_size
    c = counters(cc)
    assert c["rejected"] == 1 and c["sent"] == 4
    assert set(c) == set(cc["cc_state"].COUNTER_KEYS) and all(type(v) is int for v in c.values())


def test_streaming_group_split_across_hook_runs(cc, server, tmp_path, monkeypatch):
    """A-F3 (c)(d): batch boundaries and two hook runs never send an early usage."""
    monkeypatch.setattr(cc["cc_client"], "BATCH_SIZE", 1)
    t = session(tmp_path)
    t.prompt()
    t.call("m1", "r1", lines=3, output=30, early_output=1, tool_result_between=True)
    t.call("m2", "r2", lines=2, output=20, early_output=2)
    t.assistant_line("m3", "r3", usage={"input_tokens": 1, "output_tokens": 3}, stop=None)
    t.flush()
    run(cc, "Stop", t.path, str(tmp_path))
    assert [e["completion_tokens"] for e in server.events()] == [30, 20]
    first_state = state(cc, t.path)
    records = cc["cc_transcript"].read_window(str(t.path), 0, None).records
    assert first_state["offset"] <= t.path.stat().st_size and first_state["offset"] > records[1].start_offset
    t.assistant_line("m3", "r3", usage={"input_tokens": 1, "output_tokens": 99}, stop="end_turn")
    t.flush()
    run(cc, "Stop", t.path, str(tmp_path))
    assert [e["completion_tokens"] for e in server.events()] == [30, 20, 99]


def test_truncated_or_replaced_transcript_resets(cc, server, tmp_path):
    t = session(tmp_path)
    t.prompt()
    t.call("a1", "b1")
    t.call("a2", "b2")
    t.flush()
    run(cc, "Stop", t.path, str(tmp_path))
    assert len(server.seen) == 2
    # (a) rewritten shorter than the stored offset
    data = t.path.read_bytes()
    t.path.write_bytes(data[: len(data) // 2].rsplit(b"\n", 1)[0] + b"\n")
    t.call("a3", "b3")
    t.flush()
    run(cc, "Stop", t.path, str(tmp_path))
    assert counters(cc)["truncation_resets"] == 1 and "a3" not in json.dumps(server.events())
    assert len(server.seen) == 3
    # (b) replaced by a new file (new identity) with a larger size
    before = state(cc, t.path)
    replacement = t.path.with_name("replacement.tmp")
    replacement.write_bytes(t.path.read_bytes() + b"\n" * 10)
    os.replace(replacement, t.path)
    t.call("a4", "b4")
    t.flush()
    if state(cc, t.path)["file_id"] == [os.stat(t.path).st_dev, os.stat(t.path).st_ino]:
        pytest.skip("file system reused the file identity")
    run(cc, "Stop", t.path, str(tmp_path))
    assert counters(cc)["truncation_resets"] == 2
    assert len(server.seen) == 4 and counters(cc)["duplicates"] >= 1
    assert set(before) == set(state(cc, t.path))


def test_sessionend_backlog_is_drained(cc, server, tmp_path, monkeypatch):
    """A-F5: a SessionEnd leaves a backlog (pending); later Stop hooks of another session in the same
    directory drain it window by window."""
    tr, hook = cc["cc_transcript"], cc["cc_hook"]
    monkeypatch.setattr(tr, "RECORD_LIMIT", 3)
    old = session(tmp_path, "22222222-2222-2222-2222-222222222222")
    old.prompt()
    for _ in range(12):
        old.call()
    old.flush()
    real_budget = hook._Run.budget_left
    calls = {"n": 0}

    def one_window_budget(self):  # the SessionEnd invocation runs out of budget after one window
        calls["n"] += 1
        return real_budget(self) and calls["n"] <= 2

    monkeypatch.setattr(hook._Run, "budget_left", one_window_budget)
    run(cc, "SessionEnd", old.path, str(tmp_path))
    assert state(cc, old.path)["pending"] is True and 0 < len(server.seen) < 12
    monkeypatch.setattr(hook._Run, "budget_left", real_budget)
    new = session(tmp_path, "33333333-3333-3333-3333-333333333333")
    new.prompt()
    new.call()
    new.flush()
    for _ in range(10):
        run(cc, "Stop", new.path, str(tmp_path))
        if not state(cc, old.path)["pending"]:
            break
    assert state(cc, old.path)["pending"] is False
    assert len(server.seen) == 13
