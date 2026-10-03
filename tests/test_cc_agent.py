from __future__ import annotations

import json

import pytest

from cc_support import modules, set_home


@pytest.fixture
def agent(tmp_path, monkeypatch):
    set_home(monkeypatch, tmp_path / "home")
    return modules()["cc_agent"]


@pytest.mark.parametrize(("value", "mode", "allowlist", "expected"), [
    ("general-purpose", "deny", (), "general-purpose"),
    ("fork", "deny", (), "fork"),
    ("a-0123456789abcdef", "deny", (), "a-0123456789abcdef"),
    ("my-agent", "allowlist", ("my-agent",), "my-agent"),
])
def test_sanitize_verified_values(agent, value, mode, allowlist, expected):
    assert agent.sanitize(value, mode=mode, allowlist=allowlist, local_names=()) == expected


def test_sanitize_default_deny_and_reserved_collision(agent):
    custom = agent.sanitize("my-agent", mode="deny", allowlist=("my-agent",), local_names=())
    reserved = agent.sanitize("main", mode="allowlist", allowlist=("main",), local_names=())
    assert custom == agent.pseudonym("my-agent")
    assert reserved == agent.pseudonym("main")
    assert custom.startswith("a-") and custom == agent.pseudonym("my-agent")


def test_sanitize_rejects_bad_values_and_local_names(agent):
    assert agent.sanitize(None, mode="deny", allowlist=(), local_names=()) == "unknown"
    assert agent.sanitize("x" * 65, mode="deny", allowlist=(), local_names=()) == "unknown"
    assert agent.sanitize("host", mode="allowlist", allowlist=("host",), local_names=("HOST",)) == agent.pseudonym("host")
    assert agent.sanitize("unsafe.name", mode="allowlist", allowlist=("unsafe.name",), local_names=()) == agent.pseudonym("unsafe.name")


def test_sidecar_agent_type_is_bounded(agent, tmp_path):
    transcript = tmp_path / "session" / "subagents" / "agent-id.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("", encoding="utf-8")
    sidecar = transcript.with_suffix(".meta.json")
    sidecar.write_text(json.dumps({"agentType": "fork", "description": "secret"}), encoding="utf-8")
    assert agent.read_sidecar_agent_type(str(transcript)) == "fork"

    nested = tmp_path / "session" / "nested" / "subagents" / "agent-up.jsonl"
    nested.parent.mkdir(parents=True)
    nested.write_text("", encoding="utf-8")
    upper_sidecar = transcript.parent / "agent-up.meta.json"
    upper_sidecar.write_text(json.dumps({"agentType": "general-purpose"}), encoding="utf-8")
    assert agent.read_sidecar_agent_type(str(nested)) == "general-purpose"

    sidecar.write_text("not-json", encoding="utf-8")
    assert agent.read_sidecar_agent_type(str(transcript)) is None
    sidecar.write_bytes(b"{" + b" " * (64 * 1024) + b"}")
    assert agent.read_sidecar_agent_type(str(transcript)) is None
    assert agent.read_sidecar_agent_type(str(transcript.with_name("agent-missing.jsonl"))) is None
    outside = tmp_path / "session" / "agent-id.jsonl"
    outside.write_text("", encoding="utf-8")
    outside.with_suffix(".meta.json").write_text(json.dumps({"agentType": "fork"}), encoding="utf-8")
    assert agent.read_sidecar_agent_type(str(outside)) is None


def test_agent_config_env_overrides_file(tmp_path, monkeypatch):
    set_home(monkeypatch, tmp_path / "home")
    cfg_file = tmp_path / "home" / ".config" / "token-inspector" / "claude-code.json"
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text(json.dumps({"agent_name_mode": "allowlist", "agent_name_allowlist": ["file", 7]}))
    cc = modules()
    cfg = cc["cc_config"].load()
    assert (cfg.agent_name_mode, cfg.agent_name_allowlist) == ("allowlist", ("file",))
    monkeypatch.setenv("TOKEN_INSPECTOR_AGENT_NAME_MODE", "invalid")
    monkeypatch.setenv("TOKEN_INSPECTOR_AGENT_NAME_ALLOWLIST", '["env", 8]')
    cfg = cc["cc_config"].load()
    assert (cfg.agent_name_mode, cfg.agent_name_allowlist) == ("deny", ("env",))
