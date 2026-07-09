from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


class TokenEvent(SQLModel, table=True):
    __tablename__ = "token_events"

    id: str = Field(primary_key=True)
    project_name: str = Field(index=True)
    model: str = Field(index=True)
    role: Optional[str] = None
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    process_time_ms: Optional[int] = None
    request_size_bytes: Optional[int] = None
    response_size_bytes: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    status: str = "success"
    error_message: Optional[str] = None
    complexity: Optional[int] = None
    prompt_text: Optional[str] = None
    recorded_at: str = Field(default_factory=_now, index=True)


class PricingRule(SQLModel, table=True):
    __tablename__ = "pricing_rules"

    model: str = Field(primary_key=True)
    input_price_per_1m: float
    output_price_per_1m: float
    updated_at: str = Field(default_factory=_now)


SEED_PRICING = [
    # Anthropic
    ("claude-opus-4-7", 5.00, 25.00),
    ("claude-opus-4-6", 5.00, 25.00),
    ("claude-sonnet-4-6", 3.00, 15.00),
    ("claude-sonnet-4-5", 3.00, 15.00),
    ("claude-haiku-4-5", 1.00, 5.00),
    ("claude-haiku-3-5", 0.80, 4.00),
    # OpenAI
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
    # Google Gemini
    ("gemini/gemini-3.1-pro-preview", 2.00, 12.00),
    ("gemini/gemini-2.5-pro", 1.25, 10.00),
    ("gemini/gemini-2.5-flash", 0.30, 2.50),
    ("gemini/gemini-2.5-flash-lite", 0.10, 0.40),
    ("gemini/gemini-2.0-flash", 0.10, 0.40),
    ("gemini/gemini-2.0-flash-lite", 0.075, 0.30),
]
