"""Shared helpers for the Claude Code producer tests (O9 Phase 2).

Synthetic transcripts only (never a real transcript or copied content). Line shapes follow the C0 rules
in Plans/o9-job-correlation-cc-producer/status.md Drift Log ("C0 rule 1-9"): one line per content block
with a shared `message.id` / `requestId`; only the last line(s) of a group carry `stop_reason`; user lines
carry `promptId`; subagent files live in `<session>/subagents/agent-<id>.jsonl` with `agentId`,
`sessionId` (= parent session) and `isSidechain: true`; `<synthetic>` lines have zero usage.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

PRODUCER_DIR = Path(__file__).resolve().parents[1] / "producers" / "claude_code"
HOOK = PRODUCER_DIR / "cc_hook.py"
if str(PRODUCER_DIR) not in sys.path:
    sys.path.insert(0, str(PRODUCER_DIR))

CANARY_PATH_WIN = "C:\\Users\\ZZ-CANARY-USER\\secret\\ws"
CANARY_PATH_POSIX = "/home/zz-canary/secret/ws"
CANARY_HOST = "zz-canary-host.internal"
CANARY_TEXT = "ZZ-O9-CANARY-TEXT sk-ant-CANARYSECRET0123456789"
CANARY_BRANCH = "zz-canary-branch"
CANARY_TOOLARG = "ZZ-CANARY-TOOLARG"
CANARY_AGENT = "zz-canary-agent"


def canaries() -> list[bytes]:
    values = [CANARY_PATH_WIN, CANARY_PATH_POSIX, CANARY_HOST, "ZZ-O9-CANARY-TEXT", "sk-ant-CANARYSECRET",
              CANARY_BRANCH, CANARY_TOOLARG, CANARY_AGENT, "ZZ-CANARY-USER", "zz-canary"]
    host = socket.gethostname()
    allowed = "claude-code@hermes claude-code@windows claude-code-hook claude-code-producer-v1 anthropic claude-sonnet-4-6"
    if len(host) >= 4 and host.lower() not in allowed:
        values.append(host)
    out = []
    for value in values:
        out.append(value.encode("utf-8"))
        out.append(json.dumps(value)[1:-1].encode("utf-8"))  # JSON-escaped form (backslashes)
    return out


def modules():
    """Fresh producer modules with default constants (tests monkeypatch them)."""
    names = ["cc_config", "cc_attribution", "cc_transcript", "cc_events", "cc_state", "cc_client", "cc_hook"]
    return {name: importlib.reload(importlib.import_module(name)) for name in names}


class Transcript:
    """Append-only synthetic transcript writer."""

    def __init__(self, path: Path, session_id: Optional[str] = None, *, agent_id: Optional[str] = None,
                 cwd: str = CANARY_PATH_WIN):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or str(uuid.uuid4())
        self.agent_id = agent_id
        self.cwd = cwd
        self.prompt_id: Optional[str] = None
        self.pending: list[bytes] = []
        self.minute = 0

    def _base(self, kind: str) -> dict[str, Any]:
        self.minute += 1
        d = {"parentUuid": None, "isSidechain": self.agent_id is not None, "type": kind, "uuid": str(uuid.uuid4()),
             "timestamp": f"2026-09-28T10:{self.minute % 60:02d}:00.000Z", "userType": "external",
             "entrypoint": "cli", "cwd": self.cwd, "sessionId": self.session_id, "version": "2.1.284",
             "gitBranch": CANARY_BRANCH}
        if self.agent_id is not None:
            d["agentId"] = self.agent_id
        return d

    def raw(self, data: Any) -> "Transcript":
        self.pending.append(data if isinstance(data, bytes) else (json.dumps(data) + "\n").encode("utf-8"))
        return self

    def prompt(self, prompt_id: Optional[str] = None, *, text: str = CANARY_TEXT, with_pid: bool = True) -> str:
        self.prompt_id = prompt_id or str(uuid.uuid4())
        d = self._base("user")
        d["message"] = {"role": "user", "content": text}
        if with_pid:
            d["promptId"] = self.prompt_id
        self.raw(d)
        return self.prompt_id

    def tool_result(self, *, tool_use_id: str = "toolu_x", extra: Optional[dict] = None) -> "Transcript":
        d = self._base("user")
        d["message"] = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id,
                                                     "content": CANARY_TOOLARG + " " + CANARY_TEXT}]}
        d["promptId"] = self.prompt_id
        d["toolUseResult"] = {"stdout": CANARY_TEXT, "stderr": "", "interrupted": False}
        d.update(extra or {})
        return self.raw(d)

    def meta(self) -> "Transcript":
        d = self._base("user")
        d["message"] = {"role": "user", "content": CANARY_TEXT}
        d["isMeta"] = True
        d["promptId"] = self.prompt_id
        return self.raw(d)

    def other(self) -> "Transcript":
        return self.raw({"type": "last-prompt", "sessionId": self.session_id, "lastPrompt": CANARY_TEXT})

    def assistant_line(self, mid: str, rid: Optional[str], *, usage: dict, stop: Optional[str],
                       model: str = "claude-sonnet-4-6", block: Optional[dict] = None,
                       extra: Optional[dict] = None) -> "Transcript":
        d = self._base("assistant")
        d["message"] = {"model": model, "id": mid, "type": "message", "role": "assistant",
                        "content": [block or {"type": "text", "text": CANARY_TEXT}],
                        "stop_reason": stop, "stop_sequence": None, "usage": dict(usage, service_tier="standard")}
        if rid is not None:
            d["requestId"] = rid
        d["attributionAgent"] = CANARY_AGENT
        d.update(extra or {})
        return self.raw(d)

    def call(self, mid: Optional[str] = None, rid: Optional[str] = None, *, lines: int = 1, input_tokens: int = 10,
             output: int = 5, cache_read: int = 0, cache_create: int = 0, early_output: Optional[int] = None,
             stop: Optional[str] = "end_turn", model: str = "claude-sonnet-4-6", tool_uses: int = 0,
             tool_result_between: bool = False, extra: Optional[dict] = None, final: bool = True) -> tuple[str, str]:
        """One API call: `lines` lines sharing message.id/requestId. Earlier lines carry `early_output`
        (default: the final value) and no stop_reason; the last line carries the final usage + stop."""
        mid = mid or "msg_" + uuid.uuid4().hex
        rid = rid if rid is not None else "req_" + uuid.uuid4().hex
        usage = {"input_tokens": input_tokens, "cache_creation_input_tokens": cache_create,
                 "cache_read_input_tokens": cache_read, "output_tokens": output}
        early = dict(usage, output_tokens=early_output if early_output is not None else output)
        blocks = [{"type": "tool_use", "id": f"toolu_{mid}_{i}", "name": "Bash",
                   "input": {"command": CANARY_TOOLARG, "cwd": CANARY_PATH_POSIX}} for i in range(tool_uses)]
        for i in range(lines):
            last = i == lines - 1
            block = blocks[i] if i < len(blocks) else None
            self.assistant_line(mid, rid, usage=usage if last else early, stop=stop if (last and final) else None,
                                model=model, block=block, extra=extra)
            if tool_result_between and not last:
                self.tool_result()
        for block in blocks[lines:]:
            self.assistant_line(mid, rid, usage=usage, stop=stop if final else None, model=model, block=block,
                                extra=extra)
        return mid, rid

    def synthetic(self, *, api_error: bool = True) -> "Transcript":
        d = self._base("assistant")
        d["message"] = {"model": "<synthetic>", "id": str(uuid.uuid4()), "type": "message", "role": "assistant",
                        "content": [{"type": "text", "text": CANARY_TEXT}], "stop_reason": "stop_sequence",
                        "usage": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0}}
        if api_error:
            d["isApiErrorMessage"] = True
            d["error"] = "rate_limit"
        return self.raw(d)

    def flush(self) -> "Transcript":
        with open(self.path, "ab") as fh:
            for line in self.pending:
                fh.write(line)
        self.pending = []
        return self


def subagent_path(main: Path, agent_id: str) -> Path:
    return main.parent / main.stem / "subagents" / f"agent-{agent_id}.jsonl"


def stdin_bytes(event: str, transcript: Path, cwd: str, *, agent_transcript: Optional[Path] = None,
                session_id: Optional[str] = None) -> bytes:
    data = {"session_id": session_id or transcript.stem, "prompt_id": str(uuid.uuid4()),
            "transcript_path": str(transcript), "cwd": cwd, "permission_mode": "default",
            "hook_event_name": event, "stop_hook_active": False, "last_assistant_message": CANARY_TEXT,
            "background_tasks": [], "session_crons": []}
    if agent_transcript is not None:
        data.update({"agent_id": agent_transcript.stem[len("agent-"):], "agent_type": CANARY_AGENT,
                     "agent_transcript_path": str(agent_transcript)})
    if event == "SessionEnd":
        data = {k: data[k] for k in ("session_id", "prompt_id", "transcript_path", "cwd", "hook_event_name")}
        data["reason"] = "other"
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def set_home(monkeypatch, home: Path) -> None:
    """Point home, config dir and state dir into tmp_path; clear producer env."""
    home.mkdir(parents=True, exist_ok=True)
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    for name in ("TOKEN_INSPECTOR_URL", "TOKEN_INSPECTOR_API_KEY", "TOKEN_INSPECTOR_PROJECT_ALIASES",
                 "TOKEN_INSPECTOR_JOB_REF", "TOKEN_INSPECTOR_WORK_TYPE", "TOKEN_INSPECTOR_JOB_ATTEMPT",
                 "TI_CC_TEST_MODE"):
        monkeypatch.delenv(name, raising=False)


def make_repo(path: Path, origin: Optional[str] = None) -> Path:
    (path / ".git").mkdir(parents=True, exist_ok=True)
    config = "[core]\n\trepositoryformatversion = 0\n"
    if origin is not None:
        config += f'[remote "origin"]\n\turl = {origin}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    (path / ".git" / "config").write_text(config, encoding="utf-8")
    return path


class FakeServer:
    """Local ingest stub: records request bodies/headers; acks like the backend (dedup by client_event_id).
    `mode`: accept | status:<code> | hang | garbage | fail_from:<n> (batches from the n-th fail, 1-based)."""

    def __init__(self, mode: str = "accept"):
        self.mode = mode
        self.bodies: list[bytes] = []
        self.headers: list[dict[str, str]] = []
        self.seen: dict[str, str] = {}
        self.submissions: list[str] = []
        self.batches = 0
        self.rejected_next = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                server.batches += 1
                if server.mode == "hang":
                    threading.Event().wait(30)
                    return
                if server.mode.startswith("delay:"):
                    threading.Event().wait(float(server.mode.split(":")[1]))
                server.bodies.append(body)
                server.headers.append({k: v for k, v in self.headers.items()})
                if server.mode.startswith("status:"):
                    self.send_response(int(server.mode.split(":")[1]))
                    self.end_headers()
                    self.wfile.write(b'{"detail":"x"}')
                    return
                if server.mode.startswith("fail_from:") and server.batches >= int(server.mode.split(":")[1]):
                    self.send_response(500)
                    self.end_headers()
                    return
                if server.mode == "garbage":
                    payload = b'{"inserted": true}'
                else:
                    events = json.loads(body)["events"]
                    ins = dup = rej = 0
                    for e in events:
                        event_id = e["client_event_id"]
                        server.submissions.append(event_id)
                        if event_id in server.seen:
                            dup += 1
                        elif server.rejected_next:
                            server.rejected_next -= 1
                            rej += 1
                        else:
                            server.seen[event_id] = self.headers.get("X-Project-Name")
                            ins += 1
                    payload = json.dumps({"accepted": len(events), "inserted": ins, "duplicates": dup,
                                          "rejected": rej, "items": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def events(self) -> list[dict[str, Any]]:
        return [e for body in self.bodies for e in json.loads(body)["events"]]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def asgi_urlopen(client, loop):
    """urlopen replacement that sends the producer's request to the real ASGI app (test `client`)."""
    import io

    class _Resp(io.BytesIO):
        def __init__(self, status, data):
            super().__init__(data)
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout=None):
        headers = {k: v for k, v in request.header_items()}
        path = request.full_url.split("://", 1)[1].split("/", 1)[1]
        future = asyncio.run_coroutine_threadsafe(
            client.post("/" + path, content=request.data, headers=headers), loop)
        response = future.result(timeout=30)
        if response.status_code >= 400:
            import urllib.error
            raise urllib.error.HTTPError(request.full_url, response.status_code, "err", {}, None)
        return _Resp(response.status_code, response.content)

    return urlopen


def run_hook_subprocess(stdin: Optional[bytes], env: dict[str, str], timeout: float = 15.0, *,
                        keep_stdin_open: bool = False):
    import subprocess
    import time
    full_env = dict(os.environ)
    full_env.update(env)
    start = time.monotonic()
    if keep_stdin_open:
        proc = subprocess.Popen([sys.executable, str(HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=full_env)
        try:
            proc.wait(timeout=timeout)
            out, err = proc.stdout.read(), proc.stderr.read()
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdin.close()
        return proc.returncode, out, err, time.monotonic() - start
    result = subprocess.run([sys.executable, str(HOOK)], input=stdin or b"", capture_output=True, env=full_env,
                            timeout=timeout)
    return result.returncode, result.stdout, result.stderr, time.monotonic() - start
