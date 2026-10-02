"""Claude Code event mapping: ids, tokens, allowlist, value rules (O9 C3, C8; AC2.2, AC2.4-AC2.6)."""

from __future__ import annotations

import hashlib
import json

import pytest

from cc_support import CANARY_HOST, CANARY_PATH_WIN, Transcript, canaries, modules, set_home, subagent_path


@pytest.fixture
def cc(tmp_path, monkeypatch):
    set_home(monkeypatch, tmp_path / "home")
    return modules()


def build(cc, record, *, subagent=False, job=None, project="proj"):
    return cc["cc_events"].build_event(record, runtime="claude-code@windows", project=project,
                                       project_source="git_remote", job=job or {}, subagent=subagent)


def one_record(cc, tmp_path, **call):
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.call("msg_x", "req_x", **call)
    t.flush()
    return cc["cc_transcript"].read_window(str(t.path), 0, None).records[0]


def test_token_mapping(cc, tmp_path):
    ev = build(cc, one_record(cc, tmp_path, input_tokens=11, cache_read=222, cache_create=33, output=44))
    assert ev["prompt_tokens"] == 11  # Anthropic input_tokens exclude cache
    assert ev["cache_read_tokens"] == 222 and ev["cache_creation_tokens"] == 33 and ev["completion_tokens"] == 44
    assert ev["input_tokens_include_cache"] is False


def test_token_mapping_missing_cache_fields(cc, tmp_path):
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.assistant_line("m", "r", usage={"input_tokens": 3, "output_tokens": 4}, stop="end_turn")
    t.flush()
    ev = build(cc, cc["cc_transcript"].read_window(str(t.path), 0, None).records[0])
    assert (ev["cache_read_tokens"], ev["cache_creation_tokens"]) == (0, 0)


def test_client_event_id_from_message_and_request(cc, tmp_path):
    ce = cc["cc_events"]
    expected = "cc-" + hashlib.sha256(b"msg_1\x1freq_1").hexdigest()[:32]
    assert ce.client_event_id("msg_1", "req_1") == expected
    assert ce.client_event_id("msg_1", "req_2") != expected
    assert ce.client_event_id("msg_1", None) == "cc-" + hashlib.sha256(b"msg_1\x1f").hexdigest()[:32]
    # the same call in two sessions (resumed copy) gets the same id
    ids = []
    for session in ("sess-a", "sess-b"):
        t = Transcript(tmp_path / f"{session}.jsonl", session)
        t.prompt()
        t.call("msg_1", "req_1")
        t.flush()
        record = cc["cc_transcript"].read_window(str(t.path), 0, None).records[0]
        ev = build(cc, record)
        ids.append(ev["client_event_id"])
        body = json.dumps(ev)
        assert "msg_1" not in body and "req_1" not in body and session not in body
    assert ids == [expected, expected]


def test_events_built_only_from_allowlist(cc, tmp_path):
    ce = cc["cc_events"]
    record = one_record(cc, tmp_path, tool_uses=2, lines=2,
                        extra={"secret_field": "ZZ-SECRET", "slug": "x", "attributionSkill": "zz-canary-agent"})
    ev = build(cc, record, job={"job_ref": "20260928-101010-1", "work_type": "code", "job_attempt": 2})
    assert set(ev) <= ce.ALLOWED_FIELDS
    assert set(ev["tags"]) <= ce.TAG_KEYS
    assert "request_tool_names" not in ev and ev["tool_call_count"] == 2
    assert "ZZ-SECRET" not in json.dumps(ev) and "secret_field" not in json.dumps(ev)
    assert ev["tags"] == {"runtime": "claude-code@windows", "producer": "claude-code-hook",
                          "project_source": "git_remote", "schema": "claude-code-producer-v1",
                          "job_ref": "20260928-101010-1", "work_type": "code", "job_attempt": 2}
    for key in ("prompt_text", "task_prompt_text", "prompt_hash", "error_message", "cwd", "transcript_path"):
        assert key not in ev


def test_model_and_finish_reason_value_rules(cc, tmp_path):
    assert build(cc, one_record(cc, tmp_path, model=CANARY_HOST))["model"] == "unknown"
    assert build(cc, one_record(cc, tmp_path / "b", model="claude-opus-4-5"))["model"] == "claude-opus-4-5"
    ev = build(cc, one_record(cc, tmp_path / "c", stop="Bad Stop /x"))
    assert "finish_reason" not in ev
    canary = "zz_canary_secret"
    ev = build(cc, one_record(cc, tmp_path / "unknown", stop=canary))
    assert "finish_reason" not in ev and canary not in json.dumps(ev)
    assert build(cc, one_record(cc, tmp_path / "d"))["finish_reason"] == "end_turn"


def test_ids_are_pseudonyms(cc, tmp_path):
    ce = cc["cc_events"]
    t = Transcript(tmp_path / "s.jsonl", CANARY_PATH_WIN + "\\sess")
    pid = t.prompt("prompt-" + CANARY_PATH_WIN)
    t.call("m", "r")
    t.flush()
    ev = build(cc, cc["cc_transcript"].read_window(str(t.path), 0, None).records[0])
    assert ev["session_id"] == ce.pseudonym(CANARY_PATH_WIN + "\\sess")
    assert ev["turn_id"] == ce.pseudonym(pid)
    body = json.dumps(ev).encode()
    assert not any(c in body for c in canaries())


def test_child_event_links_to_parent(cc, tmp_path):
    ce = cc["cc_events"]
    main = tmp_path / "proj" / "sess-1.jsonl"
    parent = Transcript(main, "sess-1")
    pid = parent.prompt()
    parent.call("pm", "pr")
    parent.flush()
    parent_ev = build(cc, cc["cc_transcript"].read_window(str(main), 0, None).records[0])
    sub = Transcript(subagent_path(main, "agent01"), "sess-1", agent_id="agent01")
    sub.prompt(pid)
    sub.call("sm", "sr")
    sub.flush()
    rec = cc["cc_transcript"].read_window(str(sub.path), 0, None, subagent=True, agent_fallback="agent01").records[0]
    ev = build(cc, rec, subagent=True)
    assert ev["task_hierarchy"] == "child" and ev["role"] == "subagent"
    assert ev["session_id"] == ev["turn_id"] == ce.pseudonym("sess-1\x1fagent01")
    assert ev["parent_session_id"] == parent_ev["session_id"]
    assert ev["parent_turn_id"] == parent_ev["turn_id"]
    assert ev["parent_project_name"] == "proj"


def test_child_turn_id_is_run_id(cc, tmp_path):
    """A-F19: a subagent run without a prompt line (fork, only tool results) -> turn_id == session_id,
    whatever the reading position."""
    tr = cc["cc_transcript"]
    main = tmp_path / "proj" / "sess-2.jsonl"
    sub = Transcript(subagent_path(main, "fork1"), "sess-2", agent_id="fork1")
    sub.raw({"type": "fork-context-ref", "agentId": "fork1", "parentSessionId": "sess-2", "parentLastUuid": "u",
             "contextLength": 3})
    sub.call("f1", "g1", tool_uses=1)
    sub.prompt_id = "parent-prompt"
    sub.tool_result()
    sub.call("f2", "g2")
    sub.tool_result()
    sub.call("f3", "g3")
    sub.flush()
    full = tr.read_window(str(sub.path), 0, None, subagent=True, agent_fallback="fork1")
    # f1 closes after the first promptId was read, so it is linked like the rest of the run
    events = [build(cc, r, subagent=True) for r in full.records]
    assert [e["client_event_id"] for e in events] == [cc["cc_events"].client_event_id(m, g) for m, g in
                                                    (("f1", "g1"), ("f2", "g2"), ("f3", "g3"))]
    assert all(e["turn_id"] == e["session_id"] for e in events)
    assert {e["parent_turn_id"] for e in events} == {cc["cc_events"].pseudonym("parent-prompt")}
    # a second reader that starts mid-file with the stored parent key produces the same ids
    mid_offset = full.records[1].start_offset
    part = tr.read_window(str(sub.path), mid_offset, full.safe_turn, subagent=True, agent_fallback="fork1")
    again = build(cc, part.records[0], subagent=True)
    assert (again["session_id"], again["turn_id"], again["parent_turn_id"]) == (
        events[1]["session_id"], events[1]["turn_id"], events[1]["parent_turn_id"])
