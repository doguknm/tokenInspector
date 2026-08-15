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
| Complexity | Avg tokens by deterministic complexity tier (1–5), routing recommendations table |
| Settings | Edit/add/delete pricing rules (USD per 1M tokens) |

## Connecting Hermes or any other project

Use the helper below or adapt it into your Hermes plugin / provider wrapper.

```python
import httpx

async def push_token_event(
    model: str,
    project_name: str = "hermes",
    provider: str | None = None,
    tool_name: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    turn_id: str | None = None,
    api_request_id: str | None = None,
    client_event_id: str | None = None,
    tool_call_count: int | None = None,
    role: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    complexity: int | None = None,
    prompt_text: str | None = None,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    process_time_ms: int | None = None,
    request_size_bytes: int | None = None,
    response_size_bytes: int | None = None,
    status: str = "success",
    error_message: str | None = None,
    base_url: str = "http://127.0.0.1:8100",
):
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{base_url}/api/events",
                headers={"X-Project-Name": project_name},
                json={
                    "model": model,
                    "provider": provider,
                    "tool_name": tool_name,
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "parent_span_id": parent_span_id,
                    "turn_id": turn_id,
                    "api_request_id": api_request_id,
                    "client_event_id": client_event_id,
                    "tool_call_count": tool_call_count,
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
            )
    except Exception:
        pass  # inspector being down must never fail the calling project
```

### Batch ingest

If you already buffer events in memory, send them in one POST:

```python
await client.post(
    "http://127.0.0.1:8100/api/events/batch",
    headers={"X-Project-Name": "hermes"},
    json={"events": [event1, event2, event3]},
)
```

The batch endpoint deduplicates by `client_event_id` when present.

## Hermes integration notes

For Hermes, the best source of data is the shared model/provider hook path:

- `post_api_request` / `post_llm_call` for usage + latency + model/provider metadata
- `pre_tool_call` / `post_tool_call` for the active tool name and tool result boundary
- `session_id`, `turn_id`, and `api_request_id` for later trace correlation

That lets one telemetry sink cover:

- main agent model calls
- auxiliary calls from browser / vision / compression / search / MCP / TTS paths
- tool-scoped and session-scoped analysis in the dashboard

## API Reference

```
POST /api/events                          Ingest a token event
POST /api/events/batch                    Ingest a batch of token events
GET  /api/events?project=&model=&status=  Paginated event log

GET  /api/analytics/summary?days=7        Overall stats
GET  /api/analytics/by-project?days=30    Per-project breakdown
GET  /api/analytics/by-model?days=30      Per-model breakdown
GET  /api/analytics/by-provider?days=30   Per-provider breakdown
GET  /api/analytics/by-tool?days=30       Per-tool breakdown
GET  /api/analytics/by-role?days=30       Per-role breakdown
GET  /api/analytics/timeseries?days=30    Daily buckets for charts
GET  /api/analytics/by-complexity?days=30 Avg cost/tokens grouped by complexity tier
GET  /api/analytics/routing-recommendations Model switch suggestions by (role, complexity)

GET    /api/settings/pricing              List pricing rules
POST   /api/settings/pricing              Add or update a pricing rule
DELETE /api/settings/pricing/{model}      Remove a pricing rule
```

## Data Fields Captured Per Event

| Field | Type | Notes |
|---|---|---|
| project_name | string | From `X-Project-Name` header |
| model | string | e.g. `claude-sonnet-4-6`, `openai/gpt-4o` |
| provider | string? | e.g. `anthropic`, `openai`, `google` |
| tool_name | string? | Hermes tool / aux task name |
| session_id | string? | Session correlation id |
| trace_id | string? | Request trace id |
| span_id | string? | Child span id |
| parent_span_id | string? | Parent span id |
| turn_id | string? | Turn-level correlation id |
| api_request_id | string? | Request-scoped correlation id |
| client_event_id | string? | Optional idempotency key |
| tool_call_count | int? | Number of tool calls in the assistant response |
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
| complexity | int? | 1–5 tier; deterministic plugin metadata or legacy AI scorer |
| prompt_text | str? | Truncated prompt for AI scoring (max 3000 chars) |
| recorded_at | string | ISO8601 UTC timestamp |

## Complexity Scoring

The Hermes plugin normally sends deterministic `request-shape-v1` complexity. It is computed at request time from numeric metadata and does not require or retain raw content. The `/api/analytics/by-complexity` endpoint includes both priced and unpriced events in token averages; cost averages remain `null` when no priced observations exist.

### Legacy opt-in AI scorer

When another source pushes an event with `prompt_text` but no `complexity`, the inspector
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
