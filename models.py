from datetime import datetime, timezone
from typing import Optional

from pydantic import ConfigDict
from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


class TokenEvent(SQLModel, table=True):
    __tablename__ = "token_events"
    __table_args__ = (
        Index("idx_token_events_project_recorded", "project_name", "recorded_at"),
        Index(
            "idx_token_events_project_client_event",
            "project_name",
            "client_event_id",
            unique=True,
            sqlite_where=text("client_event_id IS NOT NULL"),
        ),
        Index("ix_token_events_project_session_turn", "project_name", "session_id", "turn_id"),
        # v11: Claude Code ids ("cc-" prefix) are unique across projects (ADR-004, O9 database.md).
        Index(
            "ux_token_events_cc_client_event",
            "client_event_id",
            unique=True,
            sqlite_where=text("substr(client_event_id, 1, 3) = 'cc-'"),
        ),
    )
    model_config = ConfigDict(protected_namespaces=())

    id: str = Field(primary_key=True)
    project_name: str = Field(index=True, max_length=64)
    client_event_id: Optional[str] = Field(default=None, max_length=128)
    recorded_at: str = Field(default_factory=_now, index=True)
    occurred_at: Optional[str] = None

    provider: Optional[str] = Field(default=None, max_length=64)
    event_type: str = Field(default="llm_request", max_length=32)
    model: str = Field(index=True, max_length=128)
    model_requested: Optional[str] = Field(default=None, max_length=128)
    pricing_model: Optional[str] = Field(default=None, max_length=128)

    session_id: Optional[str] = Field(default=None, index=True, max_length=128)
    task_id: Optional[str] = Field(default=None, max_length=128)
    turn_id: Optional[str] = Field(default=None, max_length=128)
    turn_index: Optional[int] = None
    trace_id: Optional[str] = Field(default=None, index=True, max_length=128)
    span_id: Optional[str] = Field(default=None, max_length=128)
    parent_span_id: Optional[str] = Field(default=None, max_length=128)
    api_request_id: Optional[str] = Field(default=None, max_length=128)
    tool_call_id: Optional[str] = Field(default=None, max_length=128)
    tool_name: Optional[str] = Field(default=None, max_length=128)
    role: Optional[str] = Field(default=None, max_length=64)
    environment: Optional[str] = Field(default=None, max_length=32)
    platform: Optional[str] = Field(default=None, max_length=32)
    user_id_hash: Optional[str] = Field(default=None, max_length=64)
    tags_json: Optional[str] = None

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: Optional[int] = None

    status: str = "success"
    http_status: Optional[int] = None
    finish_reason: Optional[str] = Field(default=None, max_length=64)
    error_type: Optional[str] = Field(default=None, max_length=64)
    error_message: Optional[str] = None
    attempt: int = 1
    retry_count: int = 0
    ttft_ms: Optional[int] = None
    process_time_ms: Optional[int] = None
    request_size_bytes: Optional[int] = None
    response_size_bytes: Optional[int] = None
    tool_call_count: Optional[int] = None

    estimated_cost_usd: Optional[float] = None
    cost_status: str = "unpriced"
    cost_status_reason: Optional[str] = Field(default=None, max_length=64)
    pricing_version: Optional[int] = None

    prompt_hash: Optional[str] = Field(default=None, max_length=64)
    prompt_length: Optional[int] = None
    prompt_text: Optional[str] = None
    complexity: Optional[int] = None
    complexity_method: Optional[str] = Field(default=None, max_length=32)

    request_system_chars: Optional[int] = None
    request_history_chars: Optional[int] = None
    request_tool_output_chars: Optional[int] = None
    request_file_content_chars: Optional[int] = None
    request_file_ref_count: Optional[int] = None
    request_tool_names_json: Optional[str] = None


class Task(SQLModel, table=True):
    """One Hermes turn: one user prompt -> final response (D0 decision, status.md Drift Log)."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ux_tasks_project_session_turn", "project_name", "session_id", "turn_id", unique=True),
        Index("ix_tasks_session", "session_id", "first_seen_at"),
        Index("ix_tasks_parent", "parent_task_ref"),
        Index("ix_tasks_last_seen", "last_seen_at", "id"),
        Index(
            "ix_tasks_prompt_expires",
            "prompt_expires_at",
            sqlite_where=text("prompt_text IS NOT NULL"),
        ),
        Index("ix_tasks_job_ref", "job_ref", sqlite_where=text("job_ref IS NOT NULL")),
    )

    id: str = Field(primary_key=True)  # task_ref
    project_name: str
    session_id: str
    turn_id: str
    source_task_id: Optional[str] = None
    parent_task_ref: Optional[str] = None
    root_task_ref: Optional[str] = None
    hierarchy_status: str = Field(default="unknown", sa_column_kwargs={"server_default": "unknown"})
    source: str = Field(default="ingest", sa_column_kwargs={"server_default": "ingest"})
    first_seen_at: str
    last_seen_at: str
    completion: Optional[str] = None
    completed_at: Optional[str] = None
    start_complexity: Optional[int] = None
    start_complexity_method: Optional[str] = None
    start_complexity_event_at: Optional[str] = None
    start_complexity_event_id: Optional[str] = None
    prompt_text: Optional[str] = None
    prompt_hash: Optional[str] = None
    prompt_length: Optional[int] = None
    prompt_truncated: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    prompt_redaction_version: Optional[str] = None
    prompt_captured_at: Optional[str] = None
    prompt_expires_at: Optional[str] = None
    prompt_purged_at: Optional[str] = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    # v11: the one launcher job of this task (first valid job_ref wins) and later conflicting calls.
    job_ref: Optional[str] = None
    job_ref_conflicts: int = Field(default=0, sa_column_kwargs={"server_default": "0"})


class EvaluatorRun(SQLModel, table=True):
    __tablename__ = "evaluator_runs"
    __table_args__ = (
        Index(
            "ux_evaluator_runs_active",
            "evaluator",
            unique=True,
            sqlite_where=text("status IN ('queued','running')"),
        ),
    )

    id: str = Field(primary_key=True)
    evaluator: str
    status: str
    requested_count: int
    queued_count: int
    stop_reason: Optional[str] = None
    retry_not_before: Optional[str] = None
    queued_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class TaskEvaluation(SQLModel, table=True):
    __tablename__ = "task_evaluations"
    __table_args__ = (
        Index("ix_task_evaluations_task", "task_ref"),
        Index("ix_task_evaluations_eval_time", "evaluator", "evaluated_at"),
        Index(
            "ux_task_evaluations_jev_ok",
            "task_ref",
            "rubric_version",
            "input_hash",
            unique=True,
            sqlite_where=text("evaluator = 'jev' AND status = 'ok'"),
        ),
        Index(
            "ux_task_evaluations_human",
            "task_ref",
            "rubric_version",
            "labeler",
            unique=True,
            sqlite_where=text("evaluator = 'human'"),
        ),
    )

    id: str = Field(primary_key=True)
    task_ref: str = Field(foreign_key="tasks.id")
    run_id: Optional[str] = Field(default=None, foreign_key="evaluator_runs.id")
    evaluator: str
    rubric_version: str
    status: str
    label: Optional[int] = None
    labeler: Optional[str] = None
    note: Optional[str] = None
    raw_score: Optional[float] = None
    confidence: Optional[float] = None
    probabilities_json: Optional[str] = None
    legend_json: Optional[str] = None
    model: Optional[str] = None
    provider_used: Optional[str] = None
    input_hash: Optional[str] = None
    input_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    http_attempts: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    latency_ms: Optional[int] = None
    error_type: Optional[str] = None
    evaluated_at: str


class EvaluatorAttempt(SQLModel, table=True):
    __tablename__ = "evaluator_attempts"
    __table_args__ = (
        Index("ix_evaluator_attempts_started", "started_at"),
        Index("ix_evaluator_attempts_run", "run_id"),
    )

    id: str = Field(primary_key=True)
    run_id: str = Field(foreign_key="evaluator_runs.id")
    task_ref: str = Field(foreign_key="tasks.id")
    provider: str
    status: str
    reserved_tokens: int
    reserved_cost_usd: float
    actual_input_tokens: Optional[int] = None
    actual_cost_usd: Optional[float] = None
    http_status: Optional[int] = None
    error_type: Optional[str] = None
    retry_after_s: Optional[float] = None
    started_at: str
    completed_at: Optional[str] = None


class ProviderCooldown(SQLModel, table=True):
    __tablename__ = "provider_cooldowns"

    provider: str = Field(primary_key=True)
    not_before: str
    reason: str
    updated_at: str


class RetentionState(SQLModel, table=True):
    __tablename__ = "retention_state"

    key: str = Field(primary_key=True)
    value: Optional[str] = None
    updated_at: str


class PricingRule(SQLModel, table=True):
    __tablename__ = "pricing_rules"

    model: str = Field(primary_key=True, max_length=128)
    input_price_per_1m: float
    output_price_per_1m: float
    cache_read_price_per_1m: Optional[float] = None
    cache_creation_price_per_1m: Optional[float] = None
    pricing_version: int = 1
    updated_at: str = Field(default_factory=_now)


class ModelAlias(SQLModel, table=True):
    __tablename__ = "model_aliases"

    alias: str = Field(primary_key=True, max_length=128)
    model: str = Field(index=True, max_length=128)
    updated_at: str = Field(default_factory=_now)


_BASE_PRICING = [
    ("claude-opus-4-7", 5.00, 25.00),
    ("claude-opus-4-6", 5.00, 25.00),
    ("claude-sonnet-4-6", 3.00, 15.00),
    ("claude-sonnet-4-5", 3.00, 15.00),
    ("claude-haiku-4-5", 1.00, 5.00),
    ("claude-haiku-3-5", 0.80, 4.00),
    ("openai/gpt-5.5", 5.00, 30.00),
    ("openai/gpt-5.4", 2.50, 15.00),
    ("openai/gpt-5.4-mini", 0.75, 4.50),
    ("openai/gpt-4.1", 2.00, 8.00),
    ("openai/gpt-4.1-mini", 0.40, 1.60),
    ("openai/gpt-4o", 2.50, 10.00),
    ("openai/gpt-4o-mini", 0.15, 0.60),
    ("openai/o1", 15.00, 60.00),
    ("openai/o3", 2.00, 8.00),
    ("openai/o4-mini", 1.10, 4.40),
    ("gemini/gemini-3.1-pro-preview", 2.00, 12.00),
    ("gemini/gemini-2.5-pro", 1.25, 10.00),
    ("gemini/gemini-2.5-flash", 0.30, 2.50),
    ("gemini/gemini-2.5-flash-lite", 0.10, 0.40),
    ("gemini/gemini-2.0-flash", 0.10, 0.40),
    ("gemini/gemini-2.0-flash-lite", 0.075, 0.30),
    ("typesafe-ai/jev", 0.042, 0.0),
]

SEED_PRICING = [
    {
        "model": model,
        "input_price_per_1m": input_price,
        "output_price_per_1m": output_price,
        "cache_read_price_per_1m": input_price * 0.1 if model.startswith("claude-") else None,
        "cache_creation_price_per_1m": input_price * 1.25 if model.startswith("claude-") else None,
        "pricing_version": 1,
    }
    for model, input_price, output_price in _BASE_PRICING
]
