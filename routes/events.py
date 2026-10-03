from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, literal_column, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import features
import task_store
from auth import require_ingest_auth
from complexity_scorer import score_complexity
from database import get_session
from models import TokenEvent, _now
from pricing import calculate_cost, resolve_pricing_rule

router = APIRouter(prefix="/api/events", tags=["events"])
_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SECRET_TAG_RE = re.compile(r"(key|token|secret|password|auth|credential|bearer)", re.I)
_TRUE = {"1", "true", "yes", "on"}
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:\-/]{1,128}$")
_SAFE_AGENT_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_-]*|[A-Za-z][A-Za-z0-9_-]*:[A-Za-z][A-Za-z0-9_-]*)$")
_AGENT_PSEUDONYM_RE = re.compile(r"^a-[0-9a-f]{16}$")
# Tag byte cap: measured worst-case plugin tags (O9 J2, status.md Verification Log) before raising it.
TAG_BYTES_MAX = 1024

# Launcher job context in tags (O9 J1, docs: Plans/o9-job-correlation-cc-producer/backend.md).
JOB_REF_RE = re.compile(r"^(devir-)?[0-9]{8}-[0-9]{6}-[0-9]{1,10}$")
WORK_TYPES = ("brainstorm", "review", "code", "devir", "k1", "k2", "other")
RUNTIMES = ("hermes-agent", "claude-code@windows", "claude-code@hermes", "app")
PRODUCERS = ("hermes-plugin", "claude-code-hook", "app-provider")
ATTRIBUTION_INVALID = "attribution_invalid"
# Dedup authority (O9 C10, docs/adr/004-cross-source-dedup-authority.md): one counting producer per runtime.
RUNTIME_PRODUCER = {
    "hermes-agent": "hermes-plugin",
    "claude-code@windows": "claude-code-hook",
    "claude-code@hermes": "claude-code-hook",
    "app": "app-provider",
}
CC_ID_PREFIX = "cc-"  # reserved for the Claude Code producer; unique across projects (v11 index)


def _normalize_reserved(tags: dict[str, Any]) -> dict[str, Any]:
    """Normalize the reserved job keys; drops or maps values, never raises, never adds a key net."""
    clean = dict(tags)
    clean.pop(ATTRIBUTION_INVALID, None)  # backend-only mark: a producer can never pre-set it
    removed_attribution = False
    for key, allowed in (("runtime", RUNTIMES), ("producer", PRODUCERS)):
        if key in clean and not (isinstance(clean[key], str) and clean[key] in allowed):
            del clean[key]
            removed_attribution = True
    runtime, producer = clean.get("runtime"), clean.get("producer")
    if (runtime is not None or producer is not None) and RUNTIME_PRODUCER.get(runtime) != producer:
        # invalid runtime<->producer pair (incl. a missing half): both dropped, event marked
        clean.pop("runtime", None)
        clean.pop("producer", None)
        removed_attribution = True
    if "job_ref" in clean and not (isinstance(clean["job_ref"], str) and JOB_REF_RE.fullmatch(clean["job_ref"])):
        del clean["job_ref"]
    if "work_type" in clean:
        value = clean["work_type"]
        if value is None or value == "":
            del clean["work_type"]
        else:
            normalized = value.strip().lower() if isinstance(value, str) else None
            clean["work_type"] = normalized if normalized in WORK_TYPES else "other"
    if "job_attempt" in clean and not (type(clean["job_attempt"]) is int and clean["job_attempt"] >= 1):
        del clean["job_attempt"]
    if removed_attribution:
        clean[ATTRIBUTION_INVALID] = True  # stored, never counted, never inferred as legacy
    return clean


class EventIn(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    client_event_id: Optional[str] = Field(default=None, max_length=128)
    occurred_at: Optional[str] = None
    event_type: Literal["llm_request", "tool_call", "session"] = "llm_request"

    provider: Optional[str] = Field(default=None, max_length=64)
    model: str = Field(default="unknown", min_length=1, max_length=128)
    model_requested: Optional[str] = Field(default=None, max_length=128)

    session_id: Optional[str] = Field(default=None, max_length=128)
    task_id: Optional[str] = Field(default=None, max_length=128)
    turn_id: Optional[str] = Field(default=None, max_length=128)
    turn_index: Optional[int] = Field(default=None, ge=0)
    trace_id: Optional[str] = Field(default=None, max_length=128)
    span_id: Optional[str] = Field(default=None, max_length=128)
    parent_span_id: Optional[str] = Field(default=None, max_length=128)
    api_request_id: Optional[str] = Field(default=None, max_length=128)
    tool_call_id: Optional[str] = Field(default=None, max_length=128)
    tool_name: Optional[str] = Field(default=None, max_length=128)
    role: Optional[str] = Field(default=None, max_length=64)
    environment: Optional[str] = Field(default=None, max_length=32)
    platform: Optional[str] = Field(default=None, max_length=32)
    user_id_hash: Optional[str] = Field(default=None, max_length=64)
    tags: dict[str, Any] = Field(default_factory=dict)

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_creation_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)
    input_tokens_include_cache: bool = False

    status: Literal["success", "error", "timeout", "cancelled"] = "success"
    http_status: Optional[int] = Field(default=None, ge=100, le=599)
    finish_reason: Optional[str] = Field(default=None, max_length=64)
    error_type: Optional[str] = Field(default=None, max_length=64)
    error_message: Optional[str] = Field(default=None, max_length=512)
    attempt: int = Field(default=1, ge=1)
    retry_count: int = Field(default=0, ge=0)
    ttft_ms: Optional[int] = Field(default=None, ge=0)
    process_time_ms: Optional[int] = Field(default=None, ge=0)
    request_size_bytes: Optional[int] = Field(default=None, ge=0)
    response_size_bytes: Optional[int] = Field(default=None, ge=0)
    tool_call_count: Optional[int] = Field(default=None, ge=0)

    prompt_hash: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    prompt_length: Optional[int] = Field(default=None, ge=0)
    prompt_text: Optional[str] = Field(default=None, max_length=3000)
    complexity: Optional[int] = Field(default=None, ge=1, le=5)
    complexity_method: Optional[str] = Field(default=None, max_length=32)
    agent: Optional[str] = Field(default=None, max_length=64)

    # Task fields (task = one Hermes turn; the parent is identified by its session + turn).
    task_hierarchy: Optional[Literal["root", "child", "unknown"]] = None
    parent_session_id: Optional[str] = Field(default=None, max_length=128)
    parent_turn_id: Optional[str] = Field(default=None, max_length=128)
    # Project of the parent turn for a cross-project child (O9 J6). Never stored; an invalid value
    # becomes None (today's behaviour), so it can never reject an event.
    parent_project_name: Optional[Any] = None
    task_prompt_text: Optional[str] = Field(default=None, max_length=200_000)
    task_prompt_captured_at: Optional[str] = None
    # Compared by equality and never stored; any value is accepted so metadata is never rejected.
    prompt_eligibility: Optional[str] = None

    request_system_chars: Optional[int] = Field(default=None, ge=0)
    request_history_chars: Optional[int] = Field(default=None, ge=0)
    request_tool_output_chars: Optional[int] = Field(default=None, ge=0)
    request_file_content_chars: Optional[int] = Field(default=None, ge=0)
    request_file_ref_count: Optional[int] = Field(default=None, ge=0)
    request_tool_names: Optional[list[str]] = Field(default=None, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def accept_plugin_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        aliases = {
            "event_id": "client_event_id",
            "model_actual": "model",
            "input_tokens": "prompt_tokens",
            "output_tokens": "completion_tokens",
            "cache_write_tokens": "cache_creation_tokens",
            "duration_ms": "process_time_ms",
            "prompt_chars": "prompt_length",
        }
        for source, target in aliases.items():
            if target not in data and source in data:
                data[target] = data[source]
        return data

    @field_validator("occurred_at", "task_prompt_captured_at")
    @classmethod
    def validate_occurred_at(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{info.field_name} must be ISO8601") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"{info.field_name} must include a timezone")
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @field_validator("parent_project_name", mode="before")
    @classmethod
    def validate_parent_project_name(cls, value: Any) -> Optional[str]:
        if isinstance(value, str) and _PROJECT_RE.fullmatch(value.strip().lower()):
            return value.strip().lower()
        return None

    @field_validator("agent", mode="before")
    @classmethod
    def validate_agent(cls, value: Any) -> Optional[str]:
        return _clean_agent(value)

    @field_validator("request_tool_names")
    @classmethod
    def validate_tool_names(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        for name in value:
            if not _TOOL_NAME_RE.fullmatch(name):
                raise ValueError("request_tool_names items must be 1-128 chars of [A-Za-z0-9_.:-/]")
        return sorted(set(value))


class EventBatchIn(BaseModel):
    events: list[dict[str, Any]] = Field(min_length=1, max_length=500)


def _project_name(value: str) -> str:
    normalized = value.strip().lower()
    if not _PROJECT_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=422,
            detail="X-Project-Name must match ^[a-z0-9][a-z0-9._-]{0,63}$",
        )
    return normalized


def _clean_agent(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 64:
        return None
    if (value in ("main", "custom", "unknown") or _SAFE_AGENT_RE.fullmatch(value)
            or _AGENT_PSEUDONYM_RE.fullmatch(value)):
        return value
    return None


def _clean_tags(tags: dict[str, Any]) -> str | None:
    if len(tags) > 20:
        raise ValueError("tags must contain at most 20 keys")
    clean: dict[str, Any] = {}
    for key, value in tags.items():
        key = str(key)[:64]
        if _SECRET_TAG_RE.search(key):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value if not isinstance(value, str) else value[:128]
    encoded = json.dumps(clean, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > TAG_BYTES_MAX:
        raise ValueError(f"tags must encode to at most {TAG_BYTES_MAX} bytes")
    return encoded if clean else None


def _error_text(exc: Exception) -> str:
    """Location and type only: a validation error never echoes the input (O10 F12)."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, e['loc']))}:{e['type']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
    return str(exc)


def _raw_prompts_enabled() -> bool:
    return os.environ.get("STORE_RAW_PROMPTS", "").strip().lower() in _TRUE


def _normalize_tokens(body: EventIn) -> tuple[int, int, Optional[str]]:
    prompt_tokens = body.prompt_tokens
    if body.input_tokens_include_cache:
        prompt_tokens = max(0, prompt_tokens - body.cache_read_tokens - body.cache_creation_tokens)
    reasoning_tokens = min(body.reasoning_tokens, body.completion_tokens)
    reason = "reasoning_clamped" if reasoning_tokens != body.reasoning_tokens else None
    return prompt_tokens, reasoning_tokens, reason


def _result(event: TokenEvent, *, duplicate: bool, index: Optional[int] = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": event.id,
        "client_event_id": event.client_event_id,
        "duplicate": duplicate,
        "estimated_cost_usd": event.estimated_cost_usd,
        "cost_status": event.cost_status,
        "cost_status_reason": event.cost_status_reason,
        "pricing_model": event.pricing_model,
        "pricing_version": event.pricing_version,
    }
    if index is not None:
        item["index"] = index
    return item


async def _prepare_event(body: EventIn, project_name: str, session: AsyncSession) -> TokenEvent:
    prompt_tokens, reasoning_tokens, normalization_reason = _normalize_tokens(body)
    pricing_model, rule = await resolve_pricing_rule(session, body.model, body.model_requested)
    if body.event_type == "llm_request":
        cost = calculate_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=body.completion_tokens,
            cache_read_tokens=body.cache_read_tokens,
            cache_creation_tokens=body.cache_creation_tokens,
            total_tokens=body.total_tokens,
            pricing_model=pricing_model,
            rule=rule,
            normalization_reason=normalization_reason,
        )
    else:
        cost = calculate_cost(
            prompt_tokens=0,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            total_tokens=None,
            pricing_model=None,
            rule=None,
            normalization_reason="non_llm_event",
        )

    raw_prompt = body.prompt_text
    prompt_hash = body.prompt_hash
    prompt_length = body.prompt_length
    if raw_prompt is not None:
        prompt_hash = prompt_hash or hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest()
        prompt_length = prompt_length if prompt_length is not None else len(raw_prompt)
    stored_prompt = raw_prompt if _raw_prompts_enabled() else None
    recorded_at = _now()

    return TokenEvent(
        id=str(uuid.uuid4()),
        project_name=project_name,
        client_event_id=body.client_event_id,
        recorded_at=recorded_at,
        occurred_at=body.occurred_at or recorded_at,
        event_type=body.event_type,
        provider=body.provider,
        model=body.model,
        model_requested=body.model_requested,
        pricing_model=cost.pricing_model,
        session_id=body.session_id,
        task_id=body.task_id,
        turn_id=body.turn_id,
        turn_index=body.turn_index,
        trace_id=body.trace_id,
        span_id=body.span_id,
        parent_span_id=body.parent_span_id,
        api_request_id=body.api_request_id,
        tool_call_id=body.tool_call_id,
        tool_name=body.tool_name,
        role=body.role,
        environment=body.environment,
        platform=body.platform,
        user_id_hash=body.user_id_hash,
        tags_json=_clean_tags(_normalize_reserved(body.tags)),
        prompt_tokens=prompt_tokens,
        completion_tokens=body.completion_tokens,
        cache_read_tokens=body.cache_read_tokens,
        cache_creation_tokens=body.cache_creation_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=body.total_tokens,
        status=body.status,
        http_status=body.http_status,
        finish_reason=body.finish_reason,
        error_type=body.error_type,
        error_message=body.error_message,
        attempt=body.attempt,
        retry_count=body.retry_count,
        ttft_ms=body.ttft_ms,
        process_time_ms=body.process_time_ms,
        request_size_bytes=body.request_size_bytes,
        response_size_bytes=body.response_size_bytes,
        tool_call_count=body.tool_call_count,
        estimated_cost_usd=cost.cost,
        cost_status=cost.status,
        cost_status_reason=cost.reason,
        pricing_version=cost.pricing_version,
        prompt_hash=prompt_hash,
        prompt_length=prompt_length,
        prompt_text=stored_prompt,
        complexity=body.complexity,
        complexity_method=body.complexity_method,
        request_system_chars=body.request_system_chars,
        request_history_chars=body.request_history_chars,
        request_tool_output_chars=body.request_tool_output_chars,
        request_file_content_chars=body.request_file_content_chars,
        request_file_ref_count=body.request_file_ref_count,
        request_tool_names_json=(
            json.dumps(body.request_tool_names, separators=(",", ":")) if body.request_tool_names else None
        ),
        agent=_clean_agent(body.agent),
    )


_NEXT_INGEST_SEQ = literal_column("(SELECT last_seq + 1 FROM export_state WHERE id = 1)")


async def _insert_event(
    event: TokenEvent,
    session: AsyncSession,
) -> tuple[TokenEvent, bool]:
    values = event.model_dump()
    # Export snapshot sequence (schema v12): read from the persistent high-water inside the INSERT, so it is
    # assigned under the write lock, in commit order, and never reused.
    values["ingest_seq"] = _NEXT_INGEST_SEQ
    statement = sqlite_insert(TokenEvent).values(**values)
    cross_project = bool(event.client_event_id) and event.client_event_id.startswith(CC_ID_PREFIX)
    if cross_project:
        # A conflict on either unique index (per project, or ux_token_events_cc_client_event) is a
        # duplicate: a resumed Claude Code copy resolved to another project counts once (O9 C10).
        statement = statement.on_conflict_do_nothing()
    elif event.client_event_id:
        statement = statement.on_conflict_do_nothing(
            index_elements=["project_name", "client_event_id"],
            index_where=TokenEvent.client_event_id.is_not(None),
        )
    result = await session.exec(statement)
    if result.rowcount:  # really inserted: a duplicate (ON CONFLICT DO NOTHING) consumes no number
        await session.execute(text("UPDATE export_state SET last_seq = last_seq + 1 WHERE id = 1"))

    if not event.client_event_id:
        return event, False
    lookup = select(TokenEvent).where(TokenEvent.client_event_id == event.client_event_id)
    if not cross_project:
        lookup = lookup.where(TokenEvent.project_name == event.project_name)
    stored = (await session.exec(lookup)).first()
    if stored is None:
        raise RuntimeError("idempotent insert completed without a stored row")
    return stored, stored.id != event.id


async def _derive_task(session: AsyncSession, stored: TokenEvent, body: EventIn) -> None:
    """Task derivation in the ingest transaction; task_prompt_text never reaches token_events."""
    await task_store.on_event_inserted(
        session,
        stored,
        body,
        capture_enabled=features.current().capture_enabled,
        redaction=features.redaction_options(),
        allowed_projects=features.current().allowed_projects,
    )


@router.post("", dependencies=[Depends(require_ingest_auth)])
async def create_event(
    body: EventIn,
    response: Response,
    background_tasks: BackgroundTasks,
    x_project_name: str = Header(..., alias="X-Project-Name"),
    session: AsyncSession = Depends(get_session),
):
    project_name = _project_name(x_project_name)
    try:
        candidate = await _prepare_event(body, project_name, session)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stored, duplicate = await _insert_event(candidate, session)
    if not duplicate:
        await _derive_task(session, stored, body)
    await session.commit()
    response.status_code = 200 if duplicate else 201
    if not duplicate and stored.prompt_text and stored.complexity is None:
        background_tasks.add_task(score_complexity, stored.id, stored.prompt_text)
    return _result(stored, duplicate=duplicate)


@router.post("/batch", dependencies=[Depends(require_ingest_auth)])
async def create_events_batch(
    body: EventBatchIn,
    background_tasks: BackgroundTasks,
    x_project_name: str = Header(..., alias="X-Project-Name"),
    session: AsyncSession = Depends(get_session),
):
    project_name = _project_name(x_project_name)
    items: list[dict[str, Any]] = []
    inserted = duplicates = rejected = 0
    scoring: list[TokenEvent] = []

    for index, raw in enumerate(body.events):
        try:
            parsed = EventIn.model_validate(raw)
            candidate = await _prepare_event(parsed, project_name, session)
            stored, duplicate = await _insert_event(candidate, session)
            if duplicate:
                duplicates += 1
            else:
                inserted += 1
                await _derive_task(session, stored, parsed)
                if stored.prompt_text and stored.complexity is None:
                    scoring.append(stored)
            items.append(_result(stored, duplicate=duplicate, index=index))
        except (ValidationError, ValueError) as exc:
            rejected += 1
            items.append(
                {
                    "index": index,
                    "id": None,
                    "client_event_id": raw.get("client_event_id") or raw.get("event_id"),
                    "duplicate": False,
                    "rejected": True,
                    "error": _error_text(exc)[:512],
                }
            )

    await session.commit()
    for event in scoring:
        background_tasks.add_task(score_complexity, event.id, event.prompt_text)
    return {
        "accepted": len(body.events),
        "inserted": inserted,
        "duplicates": duplicates,
        "rejected": rejected,
        "items": items,
    }


@router.get("")
async def list_events(
    project: Optional[str] = None,
    model: Optional[str] = None,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    session: AsyncSession = Depends(get_session),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    filters = []
    if project:
        filters.append(TokenEvent.project_name == project)
    if model:
        filters.append(TokenEvent.model == model)
    if status:
        filters.append(TokenEvent.status == status)
    if event_type:
        filters.append(TokenEvent.event_type == event_type)

    count_query = select(func.count()).select_from(TokenEvent)
    query = select(TokenEvent)
    for condition in filters:
        count_query = count_query.where(condition)
        query = query.where(condition)
    total = (await session.exec(count_query)).one()
    rows = (
        await session.exec(
            query.order_by(TokenEvent.recorded_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for row in rows:
        data = row.model_dump(exclude={"prompt_text", "tags_json", "ingest_seq"})
        data["tags"] = json.loads(row.tags_json) if row.tags_json else {}
        items.append(data)
    return {"items": items, "total": total, "page": page, "page_size": page_size}
