"""Claude Code transcript reader (O9 C2, C8; C0 rules 2, 3, 5, 6, 7 in status.md Drift Log)."""

from __future__ import annotations

import pytest

from cc_support import Transcript, modules, set_home, subagent_path


@pytest.fixture
def cc(tmp_path, monkeypatch):
    set_home(monkeypatch, tmp_path / "home")
    return modules()


def read(cc, path, offset=0, turn=None, **kw):
    return cc["cc_transcript"].read_window(str(path), offset, turn, **kw)


def test_streaming_lines_counted_once_with_final_usage(cc, tmp_path):
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.call("msg_a", "req_a", lines=4, output=40, early_output=3, tool_result_between=True)
    t.flush()
    win = read(cc, t.path)
    assert len(win.records) == 1
    assert win.records[0].usage["output_tokens"] == 40
    assert win.records[0].message_id == "msg_a" and win.eof


def test_group_interrupted_by_tool_result_is_one_group(cc, tmp_path):
    # C0 rule 3: tool-result lines may sit between lines of one message.id; they never finalize it
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.call("msg_a", "req_a", lines=2, output=9, early_output=1, tool_result_between=True, final=False)
    t.flush()
    win = read(cc, t.path)
    assert win.records == [] and win.safe_offset == win.start + len(t.path.read_bytes().split(b"\n")[0]) + 1


def test_synthetic_model_lines_skipped(cc, tmp_path):
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.synthetic(api_error=True)
    t.synthetic(api_error=False)
    t.call("msg_a", "req_a")
    t.flush()
    win = read(cc, t.path)
    assert [r.message_id for r in win.records] == ["msg_a"]
    assert win.counters["records_without_usage"] == 1 and win.counters["skipped_records"] == 1


@pytest.mark.parametrize("split", ["window_bytes", "record_limit"])
def test_streaming_group_split_across_reads(cc, tmp_path, monkeypatch, split):
    """A-F3/B-F2 (a)(b): the group straddles the byte window or the record limit."""
    tr = cc["cc_transcript"]
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.call("msg_a", "req_a", output=5)
    t.flush()
    b_start = t.path.stat().st_size
    t.call("msg_b", "req_b", lines=3, output=77, early_output=2, tool_result_between=True)
    t.flush()
    if split == "window_bytes":
        monkeypatch.setattr(tr, "WINDOW_BYTES", b_start + 10)  # the window ends right after b's first line
    else:
        monkeypatch.setattr(tr, "RECORD_LIMIT", 1)
    first = read(cc, t.path)
    assert [r.message_id for r in first.records] == ["msg_a"]
    assert first.safe_offset == b_start  # never past the non-final group's first line
    second = read(cc, t.path, first.safe_offset, first.safe_turn)
    assert [r.message_id for r in second.records] == ["msg_b"]
    assert second.records[0].usage["output_tokens"] == 77  # the final usage, never the early one
    assert second.records[0].turn_key == first.records[0].turn_key


def test_group_split_across_hook_runs_waits_for_final_line(cc, tmp_path):
    """(d) at the reader level: the final line is appended after the first read (EOF never finalizes)."""
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.assistant_line("msg_a", "req_a", usage={"input_tokens": 1, "output_tokens": 2}, stop=None)
    t.flush()
    first = read(cc, t.path)
    assert first.records == [] and first.safe_offset <= t.path.stat().st_size and first.eof
    t.assistant_line("msg_a", "req_a", usage={"input_tokens": 1, "output_tokens": 50}, stop="end_turn")
    t.flush()
    second = read(cc, t.path, first.safe_offset, first.safe_turn)
    assert [r.usage["output_tokens"] for r in second.records] == [50]


def test_group_final_on_next_call_or_new_prompt(cc, tmp_path):
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.call("msg_a", "req_a", output=4, final=False)  # no stop_reason at all (interrupted)
    t.prompt()  # rule (c)
    t.call("msg_b", "req_b", output=6, final=False)
    t.call("msg_c", "req_c", output=8)  # rule (b) closes b; c has stop_reason (a)
    t.flush()
    win = read(cc, t.path)
    assert [r.message_id for r in win.records] == ["msg_a", "msg_b", "msg_c"]


def test_turn_key_is_prompt_id_with_uuid_fallback(cc, tmp_path):
    t = Transcript(tmp_path / "s.jsonl")
    p1 = t.prompt()
    t.call("m1", "r1", lines=2, tool_result_between=True)
    t.call("m2", "r2")
    t.meta()  # same promptId: same turn
    t.call("m3", "r3")
    t.prompt(with_pid=False)  # older version: the prompt line uuid is the key
    t.call("m4", "r4")
    t.flush()
    records = read(cc, t.path).records
    keys = [r.turn_key for r in records]
    assert keys[0] == keys[1] == keys[2] == p1
    assert keys[3] not in (None, p1)


def test_error_record_status(cc, tmp_path):
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.call("m_ok", "r1")
    t.call("m_abort", "r2", final=False, extra={"isAbortedMidStream": True})
    t.synthetic(api_error=True)  # an API error: zero usage, not measured
    t.call("m_next", "r3")
    t.flush()
    win = read(cc, t.path)
    by_id = {r.message_id: r for r in win.records}
    assert set(by_id) == {"m_ok", "m_abort", "m_next"}
    assert by_id["m_abort"].error is True and by_id["m_ok"].error is False
    assert win.counters["records_without_usage"] == 1
    ev = cc["cc_events"].build_event(by_id["m_abort"], runtime="claude-code@windows", project="p",
                                     project_source="git_root", job={}, subagent=False)
    assert ev["status"] == "error" and ev["error_type"] == "api_error"
    ok = cc["cc_events"].build_event(by_id["m_ok"], runtime="claude-code@windows", project="p",
                                     project_source="git_root", job={}, subagent=False)
    assert ok["status"] == "success" and "error_type" not in ok


def test_partial_last_line_and_malformed_lines(cc, tmp_path):
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.raw(b"{not json\n")
    t.call("m1", "r1")
    t.flush()
    complete = t.path.stat().st_size
    with open(t.path, "ab") as fh:
        fh.write(b'{"type": "assistant", "message": {"id": "m2"')  # being written: no newline yet
    win = read(cc, t.path)
    assert [r.message_id for r in win.records] == ["m1"]
    assert win.counters["malformed_lines"] == 1
    assert win.end_offset == complete and win.safe_offset == complete  # the partial line is never consumed


def test_oversized_lines(cc, tmp_path, monkeypatch):
    """A-F5: a line above the window is parsed (ends the window); a line above the cap is skipped by scanning."""
    tr = cc["cc_transcript"]
    monkeypatch.setattr(tr, "WINDOW_BYTES", 2000)
    monkeypatch.setattr(tr, "CHUNK", 512)
    monkeypatch.setattr(tr, "OVERSIZED_LINE_MAX", 8000)
    t = Transcript(tmp_path / "s.jsonl")
    t.prompt()
    t.raw({"type": "attachment", "attachment": "x" * 5000})  # > window, < cap
    t.call("m1", "r1")
    t.raw({"type": "attachment", "attachment": "y" * 20000})  # > cap
    t.call("m2", "r2")
    t.flush()
    offset, turn, ids, oversized = 0, None, [], 0
    for _ in range(10):
        win = read(cc, t.path, offset, turn)
        ids += [r.message_id for r in win.records]
        oversized += win.counters["oversized_lines"]
        offset, turn = win.safe_offset, win.safe_turn
        if win.eof:
            break
    assert ids == ["m1", "m2"] and oversized == 1


def test_oversized_line_never_held_whole(cc, tmp_path, monkeypatch):
    tr = cc["cc_transcript"]
    monkeypatch.setattr(tr, "CHUNK", 1024)
    monkeypatch.setattr(tr, "OVERSIZED_LINE_MAX", 4096)
    path = tmp_path / "big.jsonl"
    path.write_bytes(b'{"a":"' + b"z" * 100_000 + b'"}\n')
    with open(path, "rb") as fh:
        kind, raw, size = tr._read_line(fh)
    assert kind == "oversized" and raw is None and size == path.stat().st_size


def test_subagent_records_carry_parent_prompt_and_run_id(cc, tmp_path):
    main = tmp_path / "proj" / "11111111-2222-3333-4444-555555555555.jsonl"
    parent = Transcript(main)
    pid = parent.prompt()
    parent.flush()
    sub = Transcript(subagent_path(main, "a0123456789abcdef"), parent.session_id, agent_id="a0123456789abcdef")
    sub.prompt_id = pid
    sub.prompt(pid)
    sub.call("sm1", "sr1")
    sub.prompt_id = "later-parent-prompt"  # the parent moved on; the first promptId stays the link
    sub.tool_result()
    sub.call("sm2", "sr2")
    sub.flush()
    win = read(cc, sub.path, subagent=True, agent_fallback="a0123456789abcdef")
    assert [r.turn_key for r in win.records] == [pid, pid]
    assert all(r.agent_raw == "a0123456789abcdef" and r.session_raw == parent.session_id for r in win.records)


def test_subagent_without_prompt_id_is_not_sent(cc, tmp_path):
    main = tmp_path / "proj" / "s.jsonl"
    sub = Transcript(subagent_path(main, "b1"), "sess", agent_id="b1")
    sub.prompt(with_pid=False)
    sub.call("x1", "y1")
    sub.flush()
    win = read(cc, sub.path, subagent=True, agent_fallback="b1")
    assert win.records == [] and win.counters["skipped_records"] == 1
