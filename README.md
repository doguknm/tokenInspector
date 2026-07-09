# Token Inspector

A standalone local service that collects token usage events from any connected project and provides a cross-project analytics dashboard.

## Stack

- **Backend:** FastAPI + SQLite (via aiosqlite) — single process, ~40MB RAM idle
- **Frontend:** Vanilla JS SPA + Chart.js — no build step, no framework
- **Port:** 8100

## Run

```bash
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8100
```

Open http://localhost:8100

## Dashboard Views

| View | What it shows |
|---|---|
| Overview | Stat cards, daily token+cost line chart, top models by cost, per-project table |
| Projects | Select a project → timeline, role donut, model donut, paginated event log |
| Models | Cross-project latency bar chart, cost comparison table |
| Complexity | Avg cost by complexity tier (C1–C5), routing recommendations table |
| Settings | Edit/add/delete pricing rules (USD per 1M tokens) |

## Connecting a Project

Add this helper to any project, call it after each LLM response:

```python
import httpx

async def push_token_event(
    model: str,
    role: str | None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,        # use when prompt/completion split is unavailable
    complexity: int | None = None,          # 1–5 if already computed by source project
    prompt_text: str | None = None,         # triggers AI scoring when complexity is absent
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    process_time_ms: int | None = None,
    request_size_bytes: int | None = None,
    response_size_bytes: int | None = None,
    status: str = "success",
    error_message: str | None = None,
    project_name: str = "my-project",
):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                "http://localhost:8100/api/events",
                headers={"X-Project-Name": project_name},
                json={
                    "model": model,
                    "role": role,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "complexity": complexity,
                    "prompt_text": prompt_text,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_creation_tokens": cache_creation_tokens,
                    "process_time_ms": process_time_ms,
                    "request_size_bytes": request_size_bytes,
                    "response_size_bytes": response_size_bytes,
                    "status": status,
                    "error_message": error_message,
                },
                timeout=2.0,
            )
    except Exception:
        pass  # inspector being down must never fail the calling project
```

### myprojectteam integration

Call `push_token_event()` inside `_record_token_usage()` in
`apps/dispatcher/app/dispatcher.py` after the existing `TokenUsage` insert.

## API Reference

```
POST /api/events                          Ingest a token event
GET  /api/events?project=&model=&status=  Paginated event log

GET  /api/analytics/summary?days=7        Overall stats
GET  /api/analytics/by-project?days=30   Per-project breakdown
GET  /api/analytics/by-model?days=30     Per-model breakdown
GET  /api/analytics/by-role?days=30      Per-role breakdown
GET  /api/analytics/timeseries?days=30   Daily buckets for charts
GET  /api/analytics/by-complexity?days=30    Avg cost/tokens grouped by complexity tier
GET  /api/analytics/routing-recommendations  Model switch suggestions by (role, complexity)

GET    /api/settings/pricing             List pricing rules
POST   /api/settings/pricing             Add or update a pricing rule
DELETE /api/settings/pricing/{model}     Remove a pricing rule
```

## Data Fields Captured Per Event

| Field | Type | Notes |
|---|---|---|
| project_name | string | From `X-Project-Name` header |
| model | string | e.g. `claude-sonnet-4-6`, `openai/gpt-4o` |
| role | string? | Agent role, nullable |
| prompt_tokens | int | Input token count |
| completion_tokens | int | Output token count |
| cache_read_tokens | int | Tokens served from cache |
| cache_creation_tokens | int | Tokens written to cache |
| process_time_ms | int? | LLM call latency |
| request_size_bytes | int? | Raw prompt payload size |
| response_size_bytes | int? | Raw response payload size |
| estimated_cost_usd | float? | Calculated at ingest from pricing table |
| status | string | success / error / timeout |
| error_message | string? | Set when status is error |
| complexity | int? | 1–5 complexity tier; set by source or AI scorer |
| prompt_text | str? | Truncated prompt for AI scoring (max 3000 chars) |
| recorded_at | string | ISO8601 UTC timestamp |

## AI Complexity Scoring

When an event is pushed with `prompt_text` but no `complexity`, the inspector
asynchronously calls an LLM to score complexity (1–5) and updates the stored event.
This happens in the background — the `POST /api/events` response returns immediately.

**Config:**
- `COMPLEXITY_SCORER_MODEL` — model to use (default: `claude-haiku-4-5-20251001`)
- `COMPLEXITY_SCORER_API_KEY` — API key; falls back to `ANTHROPIC_API_KEY`

If no API key is set, scoring is silently skipped. Existing events with null complexity
are not retroactively scored.

## Pre-seeded Pricing Rules

22 models across Anthropic, OpenAI, and Google Gemini are seeded on first run.
All rules are editable via the Settings view.
