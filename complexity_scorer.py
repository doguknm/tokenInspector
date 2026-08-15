import os
import json
import logging
from anthropic import AsyncAnthropic
from sqlalchemy import text
from database import AsyncSessionLocal

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a task complexity scorer. Rate the complexity of the following request on a scale of 1 to 5.
1 = trivial/lookup (single fact, simple format)
2 = simple task (one concept, clear output)
3 = moderate (multi-step, some domain knowledge)
4 = complex (architecture decisions, multiple constraints)
5 = expert (deep research, novel synthesis, large scope)

Respond with ONLY valid JSON: {"complexity": N} where N is 1, 2, 3, 4, or 5."""


async def score_complexity(event_id: str, prompt_text: str) -> None:
    if os.environ.get("STORE_RAW_PROMPTS", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    model = os.environ.get("COMPLEXITY_SCORER_MODEL", "claude-haiku-4-5-20251001")
    api_key = os.environ.get("COMPLEXITY_SCORER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("[SCORER    ] No API key for complexity scoring — skipping")
        return
    try:
        client = AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=10,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt_text}],
        )
        raw = response.content[0].text.strip()
        try:
            data = json.loads(raw)
            complexity = data.get("complexity") if isinstance(data, dict) else None
        except (json.JSONDecodeError, AttributeError):
            log.warning("[SCORER    ] Failed to parse complexity response: %r", raw)
            return
        if not isinstance(complexity, int) or not (1 <= complexity <= 5):
            log.warning("[SCORER    ] Complexity value invalid: %r", complexity)
            return
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("UPDATE token_events SET complexity = :val WHERE id = :id").bindparams(
                    val=complexity, id=event_id
                )
            )
            await session.commit()
        log.info("[SCORER    ] event=%s complexity=%d", event_id, complexity)
    except Exception as exc:
        log.warning("[SCORER    ] Scoring failed for event %s: %r", event_id, exc)
