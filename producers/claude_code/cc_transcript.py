"""Incremental Claude Code transcript reader (O9 C2, C8; rules: status.md Drift Log "C0 rule 1-9").

Reads complete JSONL lines from a byte offset, groups assistant lines by `message.id`, and returns only
**final** groups (C0 rule 3): a group is final when one of its lines has a non-null `stop_reason`, when a
later assistant line with another `message.id` was read, or when a later new-prompt user line was read.
EOF alone never finalizes a group. A non-final group is never emitted; the window's safe checkpoint stops
at its first line, so the next read sees the whole group again.

Only the values the event mapping needs leave this module (ids are pseudonymized in cc_events); message
content is only inspected to count tool_use blocks.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Optional

WINDOW_BYTES = 4 * 1024 * 1024
RECORD_LIMIT = 1000
OVERSIZED_LINE_MAX = 32 * 1024 * 1024
CHUNK = 1024 * 1024
# A single non-final group at the window start may extend the window up to this span, so a group whose
# lines are interleaved with large tool results can never stall the cursor (then it is taken as final).
GROUP_SPAN_MAX = OVERSIZED_LINE_MAX
READ_DELAY_S = 0.0  # test mode only (cc_hook, TI_CC_TEST_MODE=1)
SYNTHETIC = "<synthetic>"


@dataclass
class Record:
    message_id: str
    request_id: Optional[str]
    model: Any
    usage: dict[str, int]
    stop_reason: Any
    timestamp: Any
    tool_use_count: int
    error: bool
    session_raw: Optional[str]
    agent_raw: Optional[str]
    turn_key: Optional[str]
    start_offset: int


@dataclass
class Window:
    records: list[Record]
    start: int
    end_offset: int
    safe_offset: int
    safe_turn: Optional[str]
    eof: bool
    counters: Counter = field(default_factory=Counter)


@dataclass
class _Group:
    mid: str
    start: int
    turn_at_start: Optional[str]
    final: bool = False
    line: dict = field(default_factory=dict)
    tool_ids: set = field(default_factory=set)
    tool_blocks: int = 0
    error: bool = False


def _int(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _user_kind(d: dict) -> str:
    msg = d.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return "tool_result"
    if d.get("isMeta") or d.get("isCompactSummary"):
        return "meta"
    return "prompt"


def _read_line(fh: BinaryIO) -> tuple[str, Optional[bytes], int]:
    """('line', bytes, size) | ('oversized', None, size) | ('end', None, 0) for EOF or a partial last line."""
    first = fh.readline(CHUNK)
    if not first:
        return "end", None, 0
    if first.endswith(b"\n"):
        return "line", first, len(first)
    parts: Optional[list[bytes]] = [first]
    size = len(first)
    while True:
        chunk = fh.readline(CHUNK)
        if not chunk:
            return "end", None, 0  # a partial last line is never consumed
        size += len(chunk)
        if parts is not None:
            if size > OVERSIZED_LINE_MAX:
                parts = None  # skip by scanning to its newline without keeping it
            else:
                parts.append(chunk)
        if chunk.endswith(b"\n"):
            return ("line", b"".join(parts), size) if parts is not None else ("oversized", None, size)


def _record(group: _Group, key: Optional[str], agent_fallback: Optional[str], subagent: bool) -> Record:
    d = group.line
    msg = d.get("message") or {}
    usage = msg.get("usage") or {}
    return Record(
        message_id=group.mid,
        request_id=d.get("requestId") if isinstance(d.get("requestId"), str) else None,
        model=msg.get("model"),
        usage={
            "input_tokens": _int(usage.get("input_tokens")),
            "cache_read_input_tokens": _int(usage.get("cache_read_input_tokens")),
            "cache_creation_input_tokens": _int(usage.get("cache_creation_input_tokens")),
            "output_tokens": _int(usage.get("output_tokens")),
        },
        stop_reason=msg.get("stop_reason"),
        timestamp=d.get("timestamp"),
        tool_use_count=len(group.tool_ids) + group.tool_blocks,
        error=group.error,
        session_raw=d.get("sessionId") if isinstance(d.get("sessionId"), str) else None,
        agent_raw=(d.get("agentId") if isinstance(d.get("agentId"), str) else agent_fallback) if subagent else None,
        turn_key=key if subagent else group.turn_at_start,
        start_offset=group.start,
    )


def read_window(
    path: str,
    offset: int,
    turn: Optional[str],
    *,
    subagent: bool = False,
    agent_fallback: Optional[str] = None,
) -> Window:
    """One window from `offset`. `turn` = the turn key in effect at `offset` (main transcript) or the
    parent prompt key (subagent transcript, C0 rules 2 and 6)."""
    counters: Counter = Counter()
    records: list[Record] = []
    group: Optional[_Group] = None
    parent_key = turn if subagent else None
    pos = offset
    eof = False
    big_line = False

    def close(g: _Group) -> None:
        if subagent and parent_key is None:
            counters["skipped_records"] += 1  # never sent unlinked (C0 rule 2)
            return
        records.append(_record(g, parent_key, agent_fallback, subagent))

    with open(path, "rb") as fh:
        fh.seek(offset)
        while True:
            if READ_DELAY_S:
                time.sleep(READ_DELAY_S)
            if big_line or pos - offset >= WINDOW_BYTES or len(records) >= RECORD_LIMIT:
                stuck = group is not None and not group.final and group.start == offset
                if not stuck:
                    break
                if pos - offset >= GROUP_SPAN_MAX:
                    group.final = True
                    break
            kind, raw, size = _read_line(fh)
            if kind == "end":
                eof = True
                break
            line_start = pos
            pos += size
            if kind == "oversized":
                counters["oversized_lines"] += 1
                continue
            if size > WINDOW_BYTES:
                big_line = True  # parsed normally; it ends the window
            try:
                d = json.loads(raw)
            except Exception:
                if raw.strip():
                    counters["malformed_lines"] += 1
                continue
            if not isinstance(d, dict):
                counters["malformed_lines"] += 1
                continue
            kind_t = d.get("type")
            if kind_t == "user":
                ukind = _user_kind(d)
                if ukind == "prompt" and group is not None:
                    group.final = True  # rule (c): a new prompt never sits inside a group
                pid = d.get("promptId")
                if subagent:
                    if parent_key is None and isinstance(pid, str) and pid:
                        parent_key = pid
                elif isinstance(pid, str) and pid:
                    turn = pid
                elif ukind == "prompt" and isinstance(d.get("uuid"), str):
                    turn = d["uuid"]
                continue
            if kind_t != "assistant":
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                counters["skipped_records"] += 1
                continue
            mid = msg.get("id")
            if not isinstance(mid, str) or not mid:
                counters["skipped_records"] += 1
                continue
            if group is not None and group.mid != mid:
                group.final = True  # rule (b): calls in one transcript never interleave
                close(group)
                group = None
            if msg.get("model") == SYNTHETIC:
                counters["records_without_usage" if d.get("isApiErrorMessage") else "skipped_records"] += 1
                continue
            if not isinstance(msg.get("usage"), dict):
                counters["records_without_usage"] += 1
                continue
            if group is None:
                group = _Group(mid=mid, start=line_start, turn_at_start=turn)
            group.line = d
            if d.get("isAbortedMidStream") is True or d.get("isApiErrorMessage") is True:
                group.error = True
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if isinstance(block.get("id"), str):
                            group.tool_ids.add(block["id"])
                        else:
                            group.tool_blocks += 1
            if msg.get("stop_reason") is not None:
                group.final = True  # rule (a): this line carries the group's final usage
    if group is not None and group.final:
        close(group)
        group = None
    if group is not None:
        safe_offset, safe_turn = group.start, (parent_key if subagent else group.turn_at_start)
    else:
        safe_offset, safe_turn = pos, (parent_key if subagent else turn)
    return Window(records=records, start=offset, end_offset=pos, safe_offset=safe_offset,
                  safe_turn=safe_turn, eof=eof, counters=counters)
