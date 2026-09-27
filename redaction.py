"""task-redact-v1: free-text scrubbing for task prompts and labeller notes.

This file is byte-identical in the tokenInspector backend (`redaction.py`) and the Hermes
plugin (`task_redact.py`); both suites run tests/fixtures/redaction_vectors.json. Any rule
change bumps REDACTION_VERSION in both. Pattern-based scrubbing is not exhaustive; the
residual risk is accepted in ADR-002.
"""

from __future__ import annotations

import re
from typing import Iterable

REDACTION_VERSION = "task-redact-v1"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S
)
_KEY_SHAPES = re.compile(
    r"sk-[A-Za-z0-9_\-]{16,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}"
    r"|AIza[0-9A-Za-z_\-]{35}"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_BEARER = re.compile(r"\bBearer\s+(?!\[REDACTED)[A-Za-z0-9._~+/=\-]{16,}", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"\b([A-Za-z0-9_.\-]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Za-z0-9_.\-]*)"
    r"([\"']?\s*[:=]\s*)"
    r"(?!\[REDACTED:)(\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
    re.I,
)
_GIT_REMOTE = re.compile(
    r"\bgit@[A-Za-z0-9.\-]+:[A-Za-z0-9_.~/\-]+"
    r"|\b(?:ssh|git)://[^\s\"'<>]+"
    r"|\bhttps?://[^\s/\"'<>]+/[^\s\"'<>]*?\.git\b"
)
_URL_USERINFO = re.compile(r"://[^/\s:@\"'<>]+(?::[^/\s@\"'<>]*)?@")
_HOME_POSIX = re.compile(r"/(?:home|Users)/[^/\s\"']+/")
_HOME_WINDOWS = re.compile(r"\b[A-Za-z]:\\Users\\[^\\\s\"']+\\")
_ABS_POSIX = re.compile(r"(?<![^\s\"'(=:])/[^\s/\"'()]+(?:/[^\s/\"'()]+)+/?")
_ABS_WINDOWS = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\[^\s\"']*")
_INTERNAL_SUFFIXES = ("local", "lan", "internal", "intranet", "corp", "home.arpa", "ts.net")
_PRIVATE_IPV4 = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b"
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def _internal_host_pattern(extra_suffixes: Iterable[str]) -> re.Pattern:
    suffixes = [s.strip().lstrip(".") for s in (*_INTERNAL_SUFFIXES, *extra_suffixes) if s and s.strip()]
    alternation = "|".join(re.escape(s) for s in sorted(set(suffixes), key=len, reverse=True))
    return re.compile(rf"\b(?:[A-Za-z0-9\-]+\.)+(?:{alternation})\b", re.I)


def scrub_text(
    text: str,
    *,
    extra_host_suffixes: Iterable[str] = (),
    extra_terms: Iterable[str] = (),
) -> str:
    """Scrub secrets and private identifiers from free text. Pure and idempotent."""
    if not text:
        return text
    out = _PRIVATE_KEY.sub("[REDACTED:private_key]", text)
    out = _KEY_SHAPES.sub("[REDACTED:secret]", out)
    out = _JWT.sub("[REDACTED:jwt]", out)
    out = _BEARER.sub("Bearer [REDACTED:secret]", out)
    out = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED:secret]", out)
    out = _GIT_REMOTE.sub("[REDACTED:git_remote]", out)
    out = _URL_USERINFO.sub("://[REDACTED:userinfo]@", out)
    out = _HOME_POSIX.sub("~/", out)
    out = _HOME_WINDOWS.sub(lambda _m: "~\\", out)
    out = _ABS_POSIX.sub("[REDACTED:abs_path]", out)
    out = _ABS_WINDOWS.sub("[REDACTED:abs_path]", out)
    out = _internal_host_pattern(extra_host_suffixes).sub("[REDACTED:internal_host]", out)
    for term in extra_terms:
        term = term.strip()
        if term:
            out = re.sub(rf"\b{re.escape(term)}\b", "[REDACTED:internal_host]", out, flags=re.I)
    out = _PRIVATE_IPV4.sub("[REDACTED:private_ip]", out)
    out = _EMAIL.sub("[REDACTED:email]", out)
    return out
