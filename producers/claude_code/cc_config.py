"""Configuration for the Claude Code producer (O9 C1). Stdlib only.

URL, token, project aliases, state dir, runtime and the launcher job env contract.
The token is never logged, printed, stored in state or put in an exception message.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

PRODUCER = "claude-code-hook"
SCHEMA = "claude-code-producer-v1"
DEFAULT_URL = "http://127.0.0.1:8100"  # same loopback default as the hermes plugin

# Strict name rule (backend.md Shared definitions): exact after strip().lower(), never normalized.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Job env contract (same regexes as the plugin's job.py).
JOB_REF_RE = re.compile(r"^(devir-)?[0-9]{8}-[0-9]{6}-[0-9]{1,10}$")
WORK_TYPES = ("brainstorm", "review", "code", "devir", "k1", "k2", "other")
JOB_ATTEMPT_RE = re.compile(r"^[1-9][0-9]{0,3}$")


@dataclass
class Config:
    url: str = DEFAULT_URL
    token: Optional[str] = None
    aliases: dict[str, str] = field(default_factory=dict)
    state_dir: Path = Path(".")
    agent_name_mode: str = "deny"
    agent_name_allowlist: tuple = ()


def runtime() -> Optional[str]:
    """claude-code@windows / claude-code@hermes; None elsewhere (the hook then sends nothing)."""
    if sys.platform == "win32":
        return "claude-code@windows"
    if sys.platform.startswith("linux"):
        return "claude-code@hermes"
    return None


def config_dir() -> Path:
    return Path.home() / ".config" / "token-inspector"


def state_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "token_inspector_cc"
    return Path.home() / ".local" / "state" / "token_inspector_cc"


def valid_name(value: Any) -> Optional[str]:
    if isinstance(value, str) and NAME_RE.fullmatch(value.strip().lower()):
        return value.strip().lower()
    return None


def _aliases(raw: Any) -> dict[str, str]:
    """Absolute workspace root -> project name; invalid names are dropped (never normalized)."""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for root, name in raw.items():
        clean = valid_name(name)
        if isinstance(root, str) and root.strip() and clean:
            out[root] = clean
    return out


def _agent_allowlist(raw: Any) -> tuple:
    return tuple(value for value in raw if isinstance(value, str)) if isinstance(raw, list) else ()


def local_names() -> set[str]:
    import cc_attribution
    return cc_attribution.local_names()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _token(file_cfg: dict[str, Any]) -> Optional[str]:
    value = os.environ.get("TOKEN_INSPECTOR_API_KEY", "").strip()
    if value:
        return value
    token_file = file_cfg.get("token_file")
    path = Path(token_file).expanduser() if isinstance(token_file, str) and token_file else config_dir() / "ingest-token"
    try:
        lines = path.read_bytes().decode("utf-8").splitlines()
    except Exception:
        return None
    first = lines[0].strip() if lines else ""
    return first or None


def load() -> Config:
    file_cfg = _read_json(config_dir() / "claude-code.json")
    url = os.environ.get("TOKEN_INSPECTOR_URL", "").strip() or file_cfg.get("url") or DEFAULT_URL
    env_aliases = os.environ.get("TOKEN_INSPECTOR_PROJECT_ALIASES", "").strip()
    raw_aliases: Any = file_cfg.get("project_aliases")
    if env_aliases:
        try:
            raw_aliases = json.loads(env_aliases)
        except Exception:
            raw_aliases = {}
    mode = os.environ.get("TOKEN_INSPECTOR_AGENT_NAME_MODE", "").strip() or file_cfg.get("agent_name_mode")
    mode = mode if mode in ("deny", "allowlist") else "deny"
    raw_agent_allowlist: Any = file_cfg.get("agent_name_allowlist")
    env_agent_allowlist = os.environ.get("TOKEN_INSPECTOR_AGENT_NAME_ALLOWLIST", "").strip()
    if env_agent_allowlist:
        try:
            raw_agent_allowlist = json.loads(env_agent_allowlist)
        except Exception:
            raw_agent_allowlist = []
    return Config(
        url=str(url).rstrip("/"),
        token=_token(file_cfg),
        aliases=_aliases(raw_aliases),
        state_dir=state_dir(),
        agent_name_mode=mode,
        agent_name_allowlist=_agent_allowlist(raw_agent_allowlist),
    )


def job_tags() -> dict[str, Any]:
    """The valid launcher job keys (backend.md job env contract); invalid values are omitted silently."""
    tags: dict[str, Any] = {}
    job_ref = os.environ.get("TOKEN_INSPECTOR_JOB_REF", "")
    if JOB_REF_RE.fullmatch(job_ref):
        tags["job_ref"] = job_ref
    work_type = os.environ.get("TOKEN_INSPECTOR_WORK_TYPE", "")
    if work_type in WORK_TYPES:
        tags["work_type"] = work_type
    attempt = os.environ.get("TOKEN_INSPECTOR_JOB_ATTEMPT", "")
    if JOB_ATTEMPT_RE.fullmatch(attempt):
        tags["job_attempt"] = int(attempt)
    return tags
