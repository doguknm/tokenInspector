"""Per-transcript cursor state, lock and counters (O9 C5). Stdlib only.

State file `<state dir>/<sha256(abs transcript path)[:32]>.json` holds offsets, ids, file identity,
flags and the send context (project name + source, job keys: ids and closed shapes) — never a path,
content or token. Writes are atomic (unique temp file + os.replace) and monotonic (the offset never
regresses except for an explicit truncation reset). At most MAX_STATE_FILES files are kept.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import cc_agent

MAX_STATE_FILES = 512
MAX_STATE_BYTES = 4096
TMP_MAX_AGE_S = 3600
LOCK_STALE_S = 10.0  # 2 x HOOK_BOUND_S (cc_hook sets it from its bound)
COUNTERS = "counters.json"
COUNTER_KEYS = ("sent", "duplicates", "rejected", "failed_posts", "malformed_lines", "skipped_records",
                "records_without_usage", "oversized_lines", "lock_skips", "truncation_resets")
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_JOB_REF_RE = re.compile(r"^(devir-)?[0-9]{8}-[0-9]{6}-[0-9]{1,10}$")
_WORK_TYPE_RE = re.compile(r"^[a-z0-9]{1,16}$")
_SOURCES = ("alias", "git_remote", "git_root", "fallback")
_AGENT_SOURCES = ("main", "hook", "sidecar", "unknown")
_AGENT_RE = re.compile(
    r"^(?:main|custom|unknown|general-purpose|fork|a-[0-9a-f]{16}|"
    r"[A-Za-z][A-Za-z0-9_-]*|[A-Za-z][A-Za-z0-9_-]*:[A-Za-z][A-Za-z0-9_-]*)$"
)


def key_for(path: str) -> str:
    return hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:32]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---- lock -------------------------------------------------------------------------------------------

def acquire(state_dir: Path, key: str) -> Optional[Path]:
    """Create `<key>.lock` exclusively; a lock older than LOCK_STALE_S is taken over once. None = held."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / (key + ".lock")
    for attempt in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock
        except FileExistsError:
            if attempt or not _stale(lock):
                return None
            try:
                os.remove(lock)
            except OSError:
                return None
    return None


def _stale(lock: Path) -> bool:
    try:
        return time.time() - lock.stat().st_mtime > LOCK_STALE_S
    except OSError:
        return False


def release(lock: Optional[Path]) -> None:
    if lock is not None:
        try:
            os.remove(lock)
        except OSError:
            pass


# ---- state ------------------------------------------------------------------------------------------

def _agent_policy() -> tuple[str, tuple]:
    try:
        data = json.loads((Path.home() / ".config" / "token-inspector" / "claude-code.json").read_bytes().decode("utf-8"))
        file_cfg = data if isinstance(data, dict) else {}
    except Exception:
        file_cfg = {}
    mode = os.environ.get("TOKEN_INSPECTOR_AGENT_NAME_MODE", "").strip() or file_cfg.get("agent_name_mode")
    mode = mode if mode in ("deny", "allowlist") else "deny"
    raw_allowlist: Any = file_cfg.get("agent_name_allowlist")
    env_allowlist = os.environ.get("TOKEN_INSPECTOR_AGENT_NAME_ALLOWLIST", "").strip()
    if env_allowlist:
        try:
            raw_allowlist = json.loads(env_allowlist)
        except Exception:
            raw_allowlist = []
    allowlist = tuple(value for value in raw_allowlist if isinstance(value, str)) if isinstance(raw_allowlist, list) else ()
    return mode, allowlist


def _valid_ctx(ctx: Any) -> Optional[dict[str, Any]]:
    if not isinstance(ctx, dict):
        return None
    project, source, job = ctx.get("project"), ctx.get("project_source"), ctx.get("job")
    if not (isinstance(project, str) and _NAME_RE.fullmatch(project) and source in _SOURCES and isinstance(job, dict)):
        return None
    clean_job: dict[str, Any] = {}
    if isinstance(job.get("job_ref"), str) and _JOB_REF_RE.fullmatch(job["job_ref"]):
        clean_job["job_ref"] = job["job_ref"]
    if isinstance(job.get("work_type"), str) and _WORK_TYPE_RE.fullmatch(job["work_type"]):
        clean_job["work_type"] = job["work_type"]
    if type(job.get("job_attempt")) is int and 1 <= job["job_attempt"] <= 9999:
        clean_job["job_attempt"] = job["job_attempt"]
    clean = {"project": project, "project_source": source, "job": clean_job}
    agent_keys = ("agent", "agent_source", "agent_run_key", "agent_policy_version")
    present = [key in ctx for key in agent_keys]
    if any(present):
        agent, agent_source, run_key, version = (ctx.get(key) for key in agent_keys)
        if (all(present) and isinstance(agent, str) and len(agent) <= 64 and _AGENT_RE.fullmatch(agent)
                and agent_source in _AGENT_SOURCES and isinstance(run_key, str) and _KEY_RE.fullmatch(run_key)
                and type(version) is int and version == 1):
            if agent not in cc_agent.RESERVED:
                mode, allowlist = _agent_policy()
                import cc_attribution
                agent = cc_agent.sanitize(agent, mode=mode, allowlist=allowlist,
                                          local_names=cc_attribution.local_names())
            clean.update({"agent": agent, "agent_source": agent_source, "agent_run_key": run_key,
                          "agent_policy_version": version})
    return clean


def load(state_dir: Path, key: str) -> Optional[dict[str, Any]]:
    """The state, or None when absent, oversized or invalid (re-reading from 0 is safe)."""
    path = state_dir / (key + ".json")
    try:
        if path.stat().st_size > MAX_STATE_BYTES:
            return None
        data = json.loads(path.read_bytes().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    offset, turn, file_id = data.get("offset"), data.get("turn_uuid"), data.get("file_id")
    if not (type(offset) is int and offset >= 0):
        return None
    if turn is not None and not (isinstance(turn, str) and len(turn) <= 128):
        return None
    if not (isinstance(file_id, list) and len(file_id) == 2 and all(type(v) is int for v in file_id)):
        return None
    return {"offset": offset, "turn_uuid": turn, "file_id": file_id, "pending": data.get("pending") is True,
            "ctx": _valid_ctx(data.get("ctx"))}


def _atomic_write(path: Path, payload: bytes) -> bool:
    tmp = path.parent / f"{path.stem}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
        return True
    except OSError:  # incl. PermissionError on Windows when the target is briefly open elsewhere
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def save(state_dir: Path, key: str, *, offset: int, turn: Optional[str], file_id: list[int], pending: bool,
         ctx: Optional[dict[str, Any]], reset: bool = False) -> bool:
    """Monotonic checkpoint (caller holds the lock): never writes an offset below the stored one for the
    same file identity unless `reset` (truncation / replacement)."""
    if not reset:
        stored = load(state_dir, key)
        if stored is not None and stored["file_id"] == file_id and stored["offset"] > offset:
            return False
    data = {"offset": offset, "turn_uuid": turn, "file_id": list(file_id), "pending": bool(pending),
            "updated_at": _now()}
    clean_ctx = _valid_ctx(ctx)
    if clean_ctx is not None:
        data["ctx"] = clean_ctx
    ok = _atomic_write(state_dir / (key + ".json"), json.dumps(data, separators=(",", ":")).encode("utf-8"))
    prune(state_dir, keep=key)
    return ok


def prune(state_dir: Path, keep: str) -> None:
    """Keep at most MAX_STATE_FILES state files (oldest removed first, never a locked one or `keep`);
    remove orphan temp files older than TMP_MAX_AGE_S."""
    try:
        entries = list(os.scandir(state_dir))
    except OSError:
        return
    now = time.time()
    states = []
    for entry in entries:
        name = entry.name
        try:
            if name.endswith(".tmp"):
                if now - entry.stat().st_mtime > TMP_MAX_AGE_S:
                    os.remove(entry.path)
            elif name.endswith(".json") and _KEY_RE.fullmatch(name[:-5]):
                states.append((entry.stat().st_mtime, name[:-5]))
        except OSError:
            continue
    excess = len(states) - MAX_STATE_FILES
    if excess <= 0:
        return
    for _mtime, key in sorted(states):
        if excess <= 0:
            break
        lock = state_dir / (key + ".lock")
        if key == keep or (lock.exists() and not _stale(lock)):
            continue
        try:
            os.remove(state_dir / (key + ".json"))
            excess -= 1
        except OSError:
            continue


# ---- counters ---------------------------------------------------------------------------------------

def bump_counters(state_dir: Path, increments: dict[str, int]) -> None:
    """Integers only, best effort: a lost update is acceptable, never a crash."""
    try:
        increments = {k: int(v) for k, v in increments.items() if k in COUNTER_KEYS and int(v) > 0}
        if not increments:
            return
        path = state_dir / COUNTERS
        try:
            current = json.loads(path.read_bytes().decode("utf-8"))
            if not isinstance(current, dict):
                current = {}
        except Exception:
            current = {}
        out = {k: (current.get(k) if type(current.get(k)) is int else 0) for k in COUNTER_KEYS}
        for k, v in increments.items():
            out[k] += v
        state_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(out, separators=(",", ":")).encode("utf-8"))
    except Exception:
        pass
