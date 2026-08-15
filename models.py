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
