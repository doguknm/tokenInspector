"""Feature gates for task prompt capture and the JEV worker (backend.md §0).

A flag that is on but missing a requirement refuses to start that feature only; the service
itself still starts. The would-be configuration is always evaluated and reported as
`config_errors` so the Activation Gate preflight can run with every flag off.
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

log = logging.getLogger(__name__)

TRUE = {"1", "true", "yes", "on"}
# Exactly the fixed points of the producer's normalize_project_name; never pass an entry through
# that function, because it falls back to "hermes" on garbage.
_PROJECT_ENTRY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MIN_TOKEN_CHARS = 16
PURGE_INTERVAL_DEFAULT_S = 21600
PURGE_INTERVAL_MAX_S = 21600
JEV_DEFAULTS = {
    "JEV_DAILY_BUDGET_USD": 0.05,
    "JEV_MONTHLY_BUDGET_USD": 0.50,
    "JEV_MAX_CALLS_PER_DAY": 200,
    "JEV_MAX_RETRY_WAIT_S": 60.0,
}


@dataclass(frozen=True)
class Budget:
    day_usd: float
    month_usd: float
    max_calls_per_day: int
    max_retry_wait_s: float


@dataclass(frozen=True)
class Features:
    capture_enabled: bool
    capture_disabled_reason: Optional[str]
    jev_enabled: bool
    jev_disabled_reason: Optional[str]
    credentials_present: bool
    purge_interval_s: int
    purge_interval_raw: Optional[str]
    budget: Optional[Budget]
    config_errors: list[dict[str, str]] = field(default_factory=list)
    allowed_projects: frozenset[str] = frozenset()
    allowlist_state: str = "empty"


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in TRUE


def _token_ok(env: Mapping[str, str]) -> bool:
    return len(env.get("INGEST_TOKEN") or "") >= MIN_TOKEN_CHARS


def purge_interval(env: Mapping[str, str]) -> tuple[Optional[int], Optional[str]]:
    """Parsed interval (None when invalid) and the raw value."""
    raw = env.get("TASK_PROMPT_PURGE_INTERVAL_S")
    if raw is None or raw.strip() == "":
        return PURGE_INTERVAL_DEFAULT_S, None
    try:
        value = int(raw)
    except ValueError:
        return None, raw
    return (value if 0 <= value <= PURGE_INTERVAL_MAX_S else None), raw


def allowed_projects(env: Mapping[str, str]) -> tuple[frozenset[str], bool]:
    """Strictly normalized TASK_PROMPT_ALLOWED_PROJECTS and whether any entry was dropped."""
    kept, dropped = set(), False
    for raw in (env.get("TASK_PROMPT_ALLOWED_PROJECTS") or "").split(","):
        entry = raw.strip().lower()
        if not entry:
            continue
        if _PROJECT_ENTRY_RE.fullmatch(entry):
            kept.add(entry)
        else:
            dropped = True
    return frozenset(kept), dropped


def budget(env: Mapping[str, str]) -> Optional[Budget]:
    try:
        day = float(env.get("JEV_DAILY_BUDGET_USD", JEV_DEFAULTS["JEV_DAILY_BUDGET_USD"]))
        month = float(env.get("JEV_MONTHLY_BUDGET_USD", JEV_DEFAULTS["JEV_MONTHLY_BUDGET_USD"]))
        calls = int(env.get("JEV_MAX_CALLS_PER_DAY", JEV_DEFAULTS["JEV_MAX_CALLS_PER_DAY"]))
        wait = float(env.get("JEV_MAX_RETRY_WAIT_S", JEV_DEFAULTS["JEV_MAX_RETRY_WAIT_S"]))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (day, month, wait)):
        return None
    if day <= 0 or month <= 0 or day > month or calls < 1 or not 0 <= wait <= 3600:
        return None
    return Budget(day_usd=day, month_usd=month, max_calls_per_day=calls, max_retry_wait_s=wait)


def evaluate(env: Optional[Mapping[str, str]] = None) -> Features:
    env = os.environ if env is None else env
    errors: list[dict[str, str]] = []
    token_ok = _token_ok(env)
    interval, raw_interval = purge_interval(env)
    allowed, dropped = allowed_projects(env)
    if dropped:
        log.warning("invalid allowlist entries ignored")  # no count, no names (r2 F10)

    capture_errors = []
    if not token_ok:
        capture_errors.append("ingest_token_missing")
    # 0 is a retention-loop setting, never a valid capture configuration.
    if interval is None or interval == 0:
        capture_errors.append("invalid_purge_interval")
    # One list governs capture and JEV; appended last so existing precedence is kept.
    if not allowed:
        capture_errors.append("no_allowed_projects")
    errors += [{"feature": "capture", "error": e} for e in capture_errors]

    credentials = bool((env.get("AI_GATEWAY_API_KEY") or "").strip())
    jev_budget = budget(env)
    jev_errors = []
    if not credentials:
        jev_errors.append("credentials_missing")
    if not token_ok:
        jev_errors.append("ingest_token_missing")
    if jev_budget is None:
        jev_errors.append("invalid_budget_config")
    if not allowed:
        jev_errors.append("no_allowed_projects")
    errors += [{"feature": "jev", "error": e} for e in jev_errors]

    if not _truthy(env.get("STORE_TASK_PROMPTS")):
        capture_enabled, capture_reason = False, "not_enabled"
    elif capture_errors:
        capture_enabled, capture_reason = False, capture_errors[0]
    else:
        capture_enabled, capture_reason = True, None

    if not _truthy(env.get("JEV_ENABLED")):
        jev_enabled, jev_reason = False, "not_enabled"
    elif jev_errors:
        jev_enabled, jev_reason = False, jev_errors[0]
    else:
        jev_enabled, jev_reason = True, None

    return Features(
        capture_enabled=capture_enabled,
        capture_disabled_reason=capture_reason,
        jev_enabled=jev_enabled,
        jev_disabled_reason=jev_reason,
        credentials_present=credentials,
        purge_interval_s=interval if interval else PURGE_INTERVAL_DEFAULT_S,
        purge_interval_raw=raw_interval,
        budget=jev_budget,
        config_errors=errors,
        allowed_projects=allowed,
        allowlist_state="configured" if allowed else "empty",
    )


def host_list(env: Optional[Mapping[str, str]] = None) -> list[str]:
    env = os.environ if env is None else env
    raw = env.get("TOKEN_INSPECTOR_ALLOWED_HOSTS") or "127.0.0.1,localhost"
    return [h.strip() for h in raw.split(",") if h.strip()]


def origin_list(env: Optional[Mapping[str, str]] = None) -> set[str]:
    env = os.environ if env is None else env
    extra = env.get("TOKEN_INSPECTOR_ALLOWED_ORIGINS") or ""
    return {"http://127.0.0.1:8100", "http://localhost:8100"} | {
        o.strip().rstrip("/") for o in extra.split(",") if o.strip()
    }


def redaction_options(env: Optional[Mapping[str, str]] = None) -> dict:
    env = os.environ if env is None else env

    def split(name: str) -> tuple[str, ...]:
        return tuple(v.strip() for v in (env.get(name) or "").split(",") if v.strip())

    return {
        "extra_host_suffixes": split("REDACT_INTERNAL_HOST_SUFFIXES"),
        "extra_terms": split("REDACT_EXTRA_TERMS"),
    }


_current: Optional[Features] = None


def refresh() -> Features:
    """Re-evaluate from the environment; called once per service start."""
    global _current
    _current = evaluate()
    return _current


def current() -> Features:
    return _current if _current is not None else refresh()
