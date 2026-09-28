"""Cursor state, locks, counters, ack validation and the token (O9 C5, C6, C1; AC2.7, AC2.8)."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

import token_inspector_client
from cc_support import (CANARY_PATH_WIN, FakeServer, Transcript, canaries, modules, run_hook_subprocess, set_home,
                        stdin_bytes)

TOKEN = "zz-TOKEN-" + "q" * 24


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    set_home(monkeypatch, h)
    return h


@pytest.fixture
def cc(home, monkeypatch):
    mods = modules()
    monkeypatch.setattr(mods["cc_hook"], "HOOK_BOUND_S", 60.0)
    return mods


def sdir(cc) -> Path:
    path = cc["cc_config"].state_dir()
    assert "pytest-of" in str(path) or "tmp" in str(path).lower(), "state dir must be the test home"
    return path


def run(cc, transcript, cwd):
    cc["cc_hook"].main(io.BytesIO(stdin_bytes("Stop", transcript, cwd)))


def transcript(tmp_path, name="sess", calls=2) -> Transcript:
    t = Transcript(tmp_path / "proj" / f"{name}.jsonl", name)
    t.prompt()
    for _ in range(calls):
        t.call()
    t.flush()
    return t


# --- state -------------------------------------------------------------------------------------------------


def test_state_holds_only_offsets_and_ids(cc, tmp_path, monkeypatch):
    srv = FakeServer()
    monkeypatch.setenv("TOKEN_INSPECTOR_URL", srv.url)
    monkeypatch.setenv("TOKEN_INSPECTOR_API_KEY", TOKEN)
    try:
        t = transcript(tmp_path)
        run(cc, t.path, CANARY_PATH_WIN)
    finally:
        srv.close()
    files = [p for p in sdir(cc).iterdir()]
    states = [p for p in files if p.name != "counters.json"]
    assert len(states) == 1 and re.fullmatch(r"[0-9a-f]{32}\.json", states[0].name)
    data = json.loads(states[0].read_bytes())
    assert set(data) <= {"offset", "turn_uuid", "file_id", "pending", "updated_at", "ctx"}
    assert set(data["ctx"]) == {"project", "project_source", "job"}
    for path in files:
        raw = path.read_bytes()
        assert TOKEN.encode() not in raw and b"proj" not in raw.replace(b'"project', b"")
        assert not any(c in raw for c in canaries())
        assert str(tmp_path).encode() not in raw and json.dumps(str(tmp_path))[1:-1].encode() not in raw
    # bounded: 600 transcripts -> at most 512 state files
    st = cc["cc_state"]
    for i in range(600):
        st.save(sdir(cc), f"{i:032x}", offset=1, turn=None, file_id=[1, i], pending=False, ctx=None)
    assert len([p for p in sdir(cc).glob("*.json") if p.name != "counters.json"]) <= 512
    # an oversized or corrupt state file is treated as absent
    key = f"{1:032x}"
    (sdir(cc) / f"{key}.json").write_bytes(b"{" + b" " * 5000 + b"}")
    assert st.load(sdir(cc), key) is None
    (sdir(cc) / f"{key}.json").write_bytes(b"not json")
    assert st.load(sdir(cc), key) is None


def test_concurrent_hooks_state_safe(cc, home, tmp_path, monkeypatch):
    st = cc["cc_state"]
    # (a) two hook processes on one transcript: one holds the lock, the other exits quickly (lock_skips)
    srv = FakeServer("delay:1.5")
    try:
        t = transcript(tmp_path, "conc", calls=3)
        env = {"HOME": str(home), "USERPROFILE": str(home), "LOCALAPPDATA": str(home / "AppData" / "Local"),
               "TOKEN_INSPECTOR_URL": srv.url, "TI_CC_TEST_MODE": "1", "TI_CC_BATCH_SIZE": "1",
               "TI_CC_SEND_DEADLINE_S": "4", "TI_CC_HOOK_BOUND_S": "8"}
        full = dict(os.environ, **env)
        first = subprocess.Popen([sys.executable, str(Path(__file__).parents[1] / "producers" / "claude_code" /
                                                  "cc_hook.py")], stdin=subprocess.PIPE, env=full)
        first.stdin.write(stdin_bytes("Stop", t.path, str(tmp_path)))
        first.stdin.close()
        deadline = time.time() + 10
        while not list(sdir(cc).glob("*.lock")) and time.time() < deadline:
            time.sleep(0.05)
        code, out, err, elapsed = run_hook_subprocess(stdin_bytes("Stop", t.path, str(tmp_path)), env)
        assert (code, out, err) == (0, b"", b"") and elapsed < 3
        first.wait(timeout=20)
        assert json.loads((sdir(cc) / "counters.json").read_bytes())["lock_skips"] == 1
        srv.mode = "accept"
        run_hook_subprocess(stdin_bytes("Stop", t.path, str(tmp_path)), dict(env, TI_CC_HOOK_BOUND_S="5"))
        assert len(srv.seen) == 3  # nothing lost after a third run
    finally:
        srv.close()
    key = st.key_for(str(t.path))
    # (b) a stale lock (older than 2 x HOOK_BOUND_S) is taken over; a fresh one is respected
    lock = sdir(cc) / f"{key}.lock"
    lock.write_bytes(b"")
    assert st.acquire(sdir(cc), key) is None
    os.utime(lock, (time.time() - 3600, time.time() - 3600))
    taken = st.acquire(sdir(cc), key)
    assert taken is not None
    st.release(taken)
    # (c) a slower writer with an older offset never moves the stored offset back
    file_id = [1, 2]
    assert st.save(sdir(cc), key, offset=500, turn="t", file_id=file_id, pending=False, ctx=None)
    assert not st.save(sdir(cc), key, offset=100, turn="old", file_id=file_id, pending=True, ctx=None)
    assert st.load(sdir(cc), key)["offset"] == 500
    assert st.save(sdir(cc), key, offset=0, turn=None, file_id=file_id, pending=True, ctx=None, reset=True)
    # (d) pruning with 600 state files while one transcript's lock is held keeps that state file
    held = f"{7:032x}"
    st.save(sdir(cc), held, offset=1, turn=None, file_id=[0, 7], pending=False, ctx=None)
    os.utime(sdir(cc) / f"{held}.json", (1, 1))  # the oldest of all
    held_lock = st.acquire(sdir(cc), held)
    for i in range(1000, 1600):
        st.save(sdir(cc), f"{i:032x}", offset=1, turn=None, file_id=[0, i], pending=False, ctx=None)
    assert (sdir(cc) / f"{held}.json").exists()
    st.release(held_lock)
    # (e) unique temp files per writer, none left after success
    assert not list(sdir(cc).glob("*.tmp"))
    names = set()
    real_open = open

    def spy_open(path, mode="r", *a, **k):
        if str(path).endswith(".tmp"):
            names.add(Path(path).name)
        return real_open(path, mode, *a, **k)

    with pytest.MonkeyPatch.context() as local:  # never undo the fixture's home/env patches
        local.setattr("builtins.open", spy_open)
        for _ in range(3):
            st.save(sdir(cc), key, offset=0, turn=None, file_id=file_id, pending=True, ctx=None, reset=True)
    assert len(names) == 3 and all(re.fullmatch(rf"{key}\.\d+\.[0-9a-f]{{12}}\.tmp", n) for n in names)
    # (f) killed mid-write: the state file is the old or the new version, never partial
    before = (sdir(cc) / f"{key}.json").read_bytes()

    def dies(*a, **k):
        raise KeyboardInterrupt  # the process dies between writing the temp file and os.replace

    with pytest.MonkeyPatch.context() as local:
        local.setattr(st.os, "replace", dies)
        with pytest.raises(KeyboardInterrupt):
            st.save(sdir(cc), key, offset=900, turn="new", file_id=file_id, pending=False, ctx=None)
    assert (sdir(cc) / f"{key}.json").read_bytes() == before
    json.loads(before)


# --- client ------------------------------------------------------------------------------------------------

ACK_VECTORS = [
    ({"inserted": 1, "duplicates": 1, "rejected": 0}, 2), ({"inserted": 2, "duplicates": 0, "rejected": 0}, 2),
    ({"inserted": True, "duplicates": 1, "rejected": 0}, 2), ({"inserted": -1, "duplicates": 3, "rejected": 0}, 2),
    ({"inserted": 1.0, "duplicates": 1, "rejected": 0}, 2), ({"inserted": "1", "duplicates": 1, "rejected": 0}, 2),
    ({"inserted": 1, "duplicates": 1}, 2), ({"inserted": 1, "duplicates": 0, "rejected": 0}, 2),
    ([], 0), (None, 0), ("ok", 0), ({"inserted": 0, "duplicates": 0, "rejected": 0}, 0),
    ({"inserted": 0, "duplicates": 0, "rejected": 3}, 3),
]


@pytest.mark.parametrize(("body", "n"), ACK_VECTORS)
def test_ack_validation(cc, body, n):
    assert cc["cc_client"].valid_ack(body, n) == token_inspector_client.valid_ack(body, n)


def test_ack_validation_expected_values(cc):
    valid = cc["cc_client"].valid_ack
    assert valid({"inserted": 1, "duplicates": 1, "rejected": 0}, 2)
    assert not valid({"inserted": True, "duplicates": 1, "rejected": 0}, 2)
    assert not valid({"inserted": 1, "duplicates": 0, "rejected": 0}, 2)


def test_token_optional_and_never_logged(cc, home, tmp_path, monkeypatch, caplog, capsys):
    srv = FakeServer()
    monkeypatch.setenv("TOKEN_INSPECTOR_URL", srv.url)
    try:
        t = transcript(tmp_path, "tok-none", calls=1)
        run(cc, t.path, str(tmp_path))
        assert "X-Ingest-Token" not in srv.headers[-1] and len(srv.seen) == 1
        # token from env
        monkeypatch.setenv("TOKEN_INSPECTOR_API_KEY", TOKEN)
        t2 = transcript(tmp_path, "tok-env", calls=1)
        run(cc, t2.path, str(tmp_path))
        assert srv.headers[-1]["X-Ingest-Token"] == TOKEN
        # token from the local secret file (default location)
        monkeypatch.delenv("TOKEN_INSPECTOR_API_KEY")
        token_file = home / ".config" / "token-inspector" / "ingest-token"
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(TOKEN + "\nsecond-line\n", encoding="utf-8")
        t3 = transcript(tmp_path, "tok-file", calls=1)
        run(cc, t3.path, str(tmp_path))
        assert srv.headers[-1]["X-Ingest-Token"] == TOKEN
        # wrong token -> 401 -> exit 0, no advance
        srv.mode = "status:401"
        t4 = transcript(tmp_path, "tok-wrong", calls=1)
        run(cc, t4.path, str(tmp_path))
        st = cc["cc_state"].load(sdir(cc), cc["cc_state"].key_for(str(t4.path)))
        assert st is None or st["offset"] == 0
        env = {"HOME": str(home), "USERPROFILE": str(home), "LOCALAPPDATA": str(home / "AppData" / "Local"),
               "TOKEN_INSPECTOR_URL": srv.url, "TOKEN_INSPECTOR_API_KEY": TOKEN}
        code, out, err, _ = run_hook_subprocess(stdin_bytes("Stop", t4.path, str(tmp_path)), env)
        assert (code, out, err) == (0, b"", b"")
    finally:
        srv.close()
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err and TOKEN not in caplog.text
    for path in sdir(cc).iterdir():
        assert TOKEN.encode() not in path.read_bytes()
