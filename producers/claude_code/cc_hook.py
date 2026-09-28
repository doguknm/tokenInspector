"""Claude Code Stop / SubagentStop / SessionEnd hook for Token Inspector (O9 C7). Stdlib only.

Runs synchronously in Claude Code's hook slot and never delays it longer than HOOK_BOUND_S: a watchdog
starts before stdin is read and hard-exits with 0. No new POST starts after SEND_DEADLINE_S. The hook
writes nothing to stdout/stderr and always exits 0 (a Stop hook's output or exit code 2 would change
Claude Code's behaviour). Only transcript usage records produce events; hook stdin only locates the
transcript and the project (C10 item 3).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cc_attribution  # noqa: E402
import cc_client  # noqa: E402
import cc_config  # noqa: E402
import cc_events  # noqa: E402
import cc_state  # noqa: E402
import cc_transcript  # noqa: E402

HOOK_BOUND_S = 5.0
SEND_DEADLINE_S = 2.5
DRAIN_LIST_MAX = 200
_TEST_OVERRIDES = {  # read only when TI_CC_TEST_MODE=1 (subprocess tests)
    "TI_CC_HOOK_BOUND_S": ("cc_hook", "HOOK_BOUND_S", float),
    "TI_CC_SEND_DEADLINE_S": ("cc_hook", "SEND_DEADLINE_S", float),
    "TI_CC_WINDOW_BYTES": ("cc_transcript", "WINDOW_BYTES", int),
    "TI_CC_RECORD_LIMIT": ("cc_transcript", "RECORD_LIMIT", int),
    "TI_CC_OVERSIZED_LINE_MAX": ("cc_transcript", "OVERSIZED_LINE_MAX", int),
    "TI_CC_READ_DELAY_S": ("cc_transcript", "READ_DELAY_S", float),
    "TI_CC_BATCH_SIZE": ("cc_client", "BATCH_SIZE", int),
}


def _apply_test_overrides() -> None:
    if os.environ.get("TI_CC_TEST_MODE") != "1":
        return
    modules = {"cc_hook": sys.modules[__name__], "cc_transcript": cc_transcript, "cc_client": cc_client}
    for env, (module, attr, cast) in _TEST_OVERRIDES.items():
        if env in os.environ:
            setattr(modules[module], attr, cast(os.environ[env]))
    cc_transcript.GROUP_SPAN_MAX = cc_transcript.OVERSIZED_LINE_MAX


class _Run:
    def __init__(self, start: float, cfg: cc_config.Config, runtime: str, ctx: dict[str, Any]):
        self.start = start
        self.cfg = cfg
        self.runtime = runtime
        self.ctx = ctx
        self.counters: Counter = Counter()
        self.stopped = False  # a failed POST stops sending for this invocation

    def budget_left(self) -> bool:
        return not self.stopped and time.monotonic() - self.start < SEND_DEADLINE_S


def _is_subagent(path: str) -> bool:
    p = Path(path)
    return p.parent.name == "subagents" and p.stem.startswith("agent-")


def process_file(run: _Run, path: str, *, ctx: Optional[dict[str, Any]] = None, max_windows: int = 0) -> None:
    """Send the final records of one transcript, checkpointing after every acknowledged batch.
    `ctx` None = use the context stored in the file's state (drain); skip the file without one."""
    key = cc_state.key_for(path)
    lock = cc_state.acquire(run.cfg.state_dir, key)
    if lock is None:
        run.counters["lock_skips"] += 1
        return
    try:
        try:
            st = os.stat(path)
        except OSError:
            return
        file_id = [st.st_dev, st.st_ino]
        state = cc_state.load(run.cfg.state_dir, key)
        if ctx is None:
            ctx = state["ctx"] if state else None
            if ctx is None:
                return
        offset, turn = 0, None
        if state is not None:
            offset, turn = state["offset"], state["turn_uuid"]
            same_file = state["file_id"] == file_id or (st.st_ino == 0 and state["file_id"][1] == 0)
            if st.st_size < offset or not same_file:
                offset, turn = 0, None  # shortened or replaced: re-send is safe (idempotent insert)
                run.counters["truncation_resets"] += 1
                cc_state.save(run.cfg.state_dir, key, offset=0, turn=None, file_id=file_id, pending=True,
                              ctx=ctx, reset=True)
        subagent = _is_subagent(path)
        agent_fallback = Path(path).stem[len("agent-"):] if subagent else None

        cursor = [offset, turn]

        def checkpoint(off: int, trn: Optional[str], pending: bool) -> None:
            cursor[:] = [off, trn]
            cc_state.save(run.cfg.state_dir, key, offset=off, turn=trn, file_id=file_id, pending=pending, ctx=ctx)

        windows = 0
        while True:
            if not run.budget_left() or (max_windows and windows >= max_windows):
                checkpoint(cursor[0], cursor[1], True)  # work left over: a later hook drains it
                return
            windows += 1
            win = cc_transcript.read_window(path, offset, turn, subagent=subagent, agent_fallback=agent_fallback)
            run.counters.update(win.counters)
            pairs = []
            for record in win.records:
                event = cc_events.build_event(record, runtime=run.runtime, project=ctx["project"],
                                              project_source=ctx["project_source"], job=ctx["job"], subagent=subagent)
                if event is None:
                    run.counters["skipped_records"] += 1
                else:
                    pairs.append((record, event))
            size = max(1, cc_client.BATCH_SIZE)
            batches = [pairs[i:i + size] for i in range(0, len(pairs), size)]
            done = True
            for index, batch in enumerate(batches):
                if not run.budget_left():
                    done = False
                    break
                ack = cc_client.post_batch(run.cfg.url, run.cfg.token, ctx["project"], [e for _, e in batch])
                if ack is None:
                    run.counters["failed_posts"] += 1
                    run.stopped = True
                    done = False
                    break
                run.counters["sent"] += ack["inserted"]
                run.counters["duplicates"] += ack["duplicates"]
                run.counters["rejected"] += ack["rejected"]  # deterministic per-item failures: dropped, counted
                if index + 1 < len(batches):
                    nxt = batches[index + 1][0][0]
                    checkpoint(nxt.start_offset, nxt.turn_key, True)
            if not done:
                checkpoint(cursor[0], cursor[1], True)
                return
            offset, turn = win.safe_offset, win.safe_turn
            checkpoint(offset, turn, not win.eof)
            if win.eof or win.safe_offset == win.start:
                return
    finally:
        cc_state.release(lock)


def _drain(run: _Run, transcript: str, own_ctx: dict[str, Any], done: set[str]) -> None:
    """Bounded drain: this session's subagent files (own context), then sibling transcripts of the same
    directory whose state is pending or behind (their stored context), one window each."""
    main = Path(transcript)
    candidates: list[tuple[str, Optional[dict[str, Any]]]] = []
    sub_dir = main.parent / main.stem / "subagents"
    for directory, ctx in ((sub_dir, own_ctx), (main.parent, None)):
        try:
            with os.scandir(directory) as entries:
                for count, entry in enumerate(entries):
                    if count >= DRAIN_LIST_MAX:
                        break
                    if entry.name.endswith(".jsonl") and entry.is_file():
                        candidates.append((entry.path, ctx))
        except OSError:
            continue
    for path, ctx in candidates:
        if not run.budget_left():
            return
        key = cc_state.key_for(path)
        if key in done:
            continue
        state = cc_state.load(run.cfg.state_dir, key)
        if ctx is None:
            if state is None:
                continue
            try:
                behind = os.stat(path).st_size > state["offset"]
            except OSError:
                continue
            if not (state["pending"] or behind):
                continue
        elif state is not None:
            try:
                if os.stat(path).st_size <= state["offset"] and not state["pending"]:
                    continue
            except OSError:
                continue
        done.add(key)
        process_file(run, path, ctx=ctx, max_windows=1)


def _run(start: float, stdin: BinaryIO) -> Counter:
    raw = stdin.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        return Counter()
    runtime = cc_config.runtime()
    if runtime is None:
        return Counter()
    transcript = data.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return Counter()
    cfg = cc_config.load()
    cc_state.LOCK_STALE_S = 2 * HOOK_BOUND_S
    project, source = cc_attribution.resolve(data.get("cwd"), cfg.aliases)
    ctx = {"project": project, "project_source": source, "job": cc_config.job_tags()}
    run = _Run(start, cfg, runtime, ctx)
    targets = []
    agent = data.get("agent_transcript_path")
    if data.get("hook_event_name") == "SubagentStop" and isinstance(agent, str) and agent:
        targets.append(agent)
    targets.append(transcript)
    done: set[str] = set()
    for target in targets:
        done.add(cc_state.key_for(target))
        process_file(run, target, ctx=ctx)
    _drain(run, transcript, ctx, done)
    cc_state.bump_counters(cfg.state_dir, run.counters)
    return run.counters


def main(stdin: Optional[BinaryIO] = None) -> int:
    start = time.monotonic()
    watchdog = None
    try:
        _apply_test_overrides()
        watchdog = threading.Timer(HOOK_BOUND_S, os._exit, args=(0,))
        watchdog.daemon = True
        watchdog.start()  # first: a blocked stdin is bounded too
        _run(start, stdin if stdin is not None else sys.stdin.buffer)
    except BaseException:
        pass
    finally:
        if watchdog is not None:
            watchdog.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main())
