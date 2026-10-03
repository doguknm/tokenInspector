from __future__ import annotations

import io
import json

import pytest

from cc_support import FakeServer, Transcript, modules, set_home, stdin_bytes, subagent_path


@pytest.mark.parametrize("raw", [
    "/home/private/work", "https://secret.example/path", "person@example.com", "host.internal", "hostname",
])
def test_raw_agent_canaries_never_leave_or_persist(tmp_path, monkeypatch, raw):
    set_home(monkeypatch, tmp_path / "home")
    cc = modules()
    monkeypatch.setattr(cc["cc_hook"], "HOOK_BOUND_S", 60.0)
    server = FakeServer()
    monkeypatch.setenv("TOKEN_INSPECTOR_URL", server.url)
    try:
        main = Transcript(tmp_path / "session.jsonl", "session")
        parent = main.prompt()
        main.call("main-m", "main-r")
        main.flush()
        sub = Transcript(subagent_path(main.path, "run"), "session", agent_id="run")
        sub.prompt(parent)
        sub.call("sub-m", "sub-r")
        sub.flush()
        data = json.loads(stdin_bytes("SubagentStop", main.path, str(tmp_path), agent_transcript=sub.path))
        data["agent_type"] = raw
        assert cc["cc_hook"].main(io.BytesIO(json.dumps(data).encode())) == 0
        replacement = cc["cc_agent"].pseudonym(raw).encode()
        assert replacement in b"".join(server.bodies)
        state_payloads = [p.read_bytes() for p in cc["cc_config"].state_dir().glob("*.json")]
        assert replacement in b"".join(state_payloads)
        for payload in [*server.bodies, *state_payloads]:
            assert raw.encode() not in payload
    finally:
        server.close()
