"""Privacy-safe Claude Code agent label resolution. Stdlib only."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

AGENT_MAX = 64
RESERVED = ("main", "custom", "unknown")
BUILTIN = ("general-purpose", "fork")
SAFE_AGENT_RE = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9_-]*|[A-Za-z][A-Za-z0-9_-]*:[A-Za-z][A-Za-z0-9_-]*)$"
)
_PSEUDONYM_RE = re.compile(r"^a-[0-9a-f]{16}$")
_META_MAX = 64 * 1024


def pseudonym(value: str) -> str:
    return "a-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def sanitize(value, *, mode, allowlist, local_names) -> str:
    if not isinstance(value, str) or not value or len(value) > AGENT_MAX:
        return "unknown"
    if value in RESERVED:
        return pseudonym(value)
    if value in BUILTIN:
        return value
    if _PSEUDONYM_RE.fullmatch(value):
        return value
    local = {name.lower() for name in local_names if isinstance(name, str)}
    if (mode == "allowlist" and SAFE_AGENT_RE.fullmatch(value) and value in allowlist
            and value.lower() not in local):
        return value
    return pseudonym(value)


def read_sidecar_agent_type(path: str) -> Optional[str]:
    try:
        transcript = Path(path)
        if transcript.parent.name != "subagents" or not transcript.name.startswith("agent-"):
            return None

        def read(candidate: Path) -> tuple[bool, Optional[str]]:
            try:
                if candidate.stat().st_size > _META_MAX:
                    return True, None
                data = json.loads(candidate.read_bytes().decode("utf-8"))
            except FileNotFoundError:
                return False, None
            except Exception:
                return True, None
            value = data.get("agentType") if isinstance(data, dict) else None
            return True, value if isinstance(value, str) and value else None

        sidecar = transcript.with_suffix(".meta.json")
        found, value = read(sidecar)
        if found:
            return value
        candidate = transcript.parent.parent
        for _ in range(8):
            found, value = read(candidate / "subagents" / sidecar.name)
            if found:
                return value
            parent = candidate.parent
            if parent == candidate:
                break
            candidate = parent
        return None
    except Exception:
        return None
