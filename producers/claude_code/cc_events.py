"""Transcript record -> ingest event with an explicit field allowlist (O9 C3, C8). Stdlib only.

Agent names are sent only as a sanitized label (main / a verified built-in / an allowlisted custom name /
a stable pseudonym / unknown); raw names, ids and locations never leave. Prompt/response text, tool inputs,
hostnames and raw Claude Code ids never leave the machine.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Optional

import cc_config
import cc_complexity

ALLOWED_FIELDS = frozenset({
    "client_event_id", "event_type", "occurred_at", "provider", "model", "session_id", "turn_id",
    "task_hierarchy", "parent_session_id", "parent_turn_id", "parent_project_name", "role",
    "prompt_tokens", "cache_read_tokens", "cache_creation_tokens", "completion_tokens",
    "input_tokens_include_cache", "status", "error_type", "finish_reason", "tool_call_count", "tags", "agent",
    "complexity", "complexity_method",
})
TAG_KEYS = frozenset({"runtime", "producer", "job_ref", "work_type", "job_attempt", "project_source", "schema"})
MODEL_RE = re.compile(r"^claude-[a-z0-9.-]{1,64}$")
FINISH_REASONS = ("end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal",
                  "model_context_window_exceeded")
PROJECT_SOURCES = ("alias", "git_remote", "git_root", "fallback")


def pseudonym(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def client_event_id(message_id: str, request_id: Optional[str]) -> str:
    """`cc-` + hash of the provider message id and request id; never derived from the session."""
    return "cc-" + hashlib.sha256((message_id + "\x1f" + (request_id or "")).encode("utf-8")).hexdigest()[:32]


def _occurred_at(value: Any) -> Optional[str]:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_event(
    record: Any,
    *,
    runtime: str,
    project: str,
    project_source: str,
    job: dict[str, Any],
    subagent: bool,
    agent: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """One llm_request event, or None when the record lacks the ids a task needs."""
    if not record.session_raw:
        return None
    usage = record.usage
    event: dict[str, Any] = {
        "client_event_id": client_event_id(record.message_id, record.request_id),
        "event_type": "llm_request",
        "provider": "anthropic",
        "model": record.model if isinstance(record.model, str) and MODEL_RE.fullmatch(record.model) else "unknown",
        "prompt_tokens": usage["input_tokens"],  # Anthropic reports input without cache
        "cache_read_tokens": usage["cache_read_input_tokens"],
        "cache_creation_tokens": usage["cache_creation_input_tokens"],
        "completion_tokens": usage["output_tokens"],
        "input_tokens_include_cache": False,
        "tool_call_count": record.tool_use_count,
        "status": "error" if record.error else "success",
    }
    if record.error:
        event["error_type"] = "api_error"
    if isinstance(agent, str) and agent:
        event["agent"] = agent
    if record.usage_valid:
        tier = cc_complexity.input_size_tier(usage["input_tokens"], usage["cache_read_input_tokens"],
                                             usage["cache_creation_input_tokens"])
        if tier is not None:
            event["complexity"] = tier
            event["complexity_method"] = cc_complexity.METHOD
    occurred = _occurred_at(record.timestamp)
    if occurred:
        event["occurred_at"] = occurred
    if record.stop_reason in FINISH_REASONS:
        event["finish_reason"] = record.stop_reason
    if subagent:
        if not record.agent_raw or not record.turn_key:
            return None  # never sent unlinked (C0 rule 2)
        child = pseudonym(record.session_raw + "\x1f" + record.agent_raw)
        event.update({
            "session_id": child,
            "turn_id": child,  # one subagent run = one child task; the run id is its turn (A-F19)
            "task_hierarchy": "child",
            "role": "subagent",
            "parent_session_id": pseudonym(record.session_raw),
            "parent_turn_id": pseudonym(record.turn_key),
            "parent_project_name": project,
        })
    else:
        event.update({"session_id": pseudonym(record.session_raw), "task_hierarchy": "root", "role": "primary"})
        if record.turn_key:
            event["turn_id"] = pseudonym(record.turn_key)
    tags: dict[str, Any] = {
        "runtime": runtime,
        "producer": cc_config.PRODUCER,
        "project_source": project_source if project_source in PROJECT_SOURCES else "fallback",
        "schema": cc_config.SCHEMA,
    }
    tags.update({k: v for k, v in job.items() if k in ("job_ref", "work_type", "job_attempt")})
    event["tags"] = {k: v for k, v in tags.items() if k in TAG_KEYS}
    return {k: v for k, v in event.items() if k in ALLOWED_FIELDS}  # final allowlist filter
