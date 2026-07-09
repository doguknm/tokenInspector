# CLAUDE.md

Guidance for Claude Code and other AI coding assistants when working in this repository.

## Project Overview

A standalone local analytics service that collects token usage events from any connected project via HTTP POST and stores them in a SQLite database. A vanilla JS dashboard visualizes cross-project token consumption, cost estimates, and LLM latency. Connected projects push one event per LLM call; the inspector calculates cost at ingest time from a user-maintained pricing table.

| Layer | Technology |
|---|---|
| Backend | FastAPI, Python 3.13, SQLModel, aiosqlite |
| Database | SQLite (single file, auto-created on first run) |
| Frontend | Vanilla JS SPA, Chart.js (no build step) |
| HTTP client | httpx (for integration helpers) |

## Read First

**`AGENTS.md`** in the repo root is the authoritative architecture reference. Read it before touching the backend. It documents every module, the full data model, all API endpoints, the cost calculation flow, integration patterns, and known gotchas.

## Critical Technical Patterns

### AsyncSession must be SQLModel's, not SQLAlchemy's
`sqlalchemy.ext.asyncio.AsyncSession` does not expose `.exec()` — that method is SQLModel-specific. Always import `from sqlmodel.ext.asyncio.session import AsyncSession` in all route files and `database.py`. Using the SQLAlchemy variant causes `AttributeError: 'AsyncSession' object has no attribute 'exec'` at startup.

### Run with `python -m uvicorn`, not bare `uvicorn`
On Windows the `uvicorn` executable may not be on PATH even after `pip install`. Always start the server with `python -m uvicorn main:app --host 0.0.0.0 --port 8100`. Never use a bare `uvicorn` invocation in commands or scripts.

### Run from the project root — SQLite path is relative to CWD
`database.py` resolves `DB_PATH` as `"token_inspector.db"` relative to the current working directory. If you start uvicorn from a different directory, the DB file is created there instead. Always `cd` to `tokenInspector/` before starting the server, or set `DB_PATH` to an absolute path via environment variable.

### model key in events must exactly match pricing_rules primary key
Cost is calculated at ingest by looking up `body.model` in the `pricing_rules` table. The lookup is case-sensitive and exact. If a project sends `Claude-Sonnet-4-6` but the rule is stored as `claude-sonnet-4-6`, `estimated_cost_usd` is stored as `null`. The model key format follows the same provider-prefix convention as myprojectteam: `claude-*`, `openai/*`, `gemini/*`.

### Pricing rules are seeded only once on first run
`_seed_pricing()` in `main.py` checks `COUNT(*) FROM pricing_rules` and skips seeding if any rows exist. If the DB already exists from a previous run with no pricing data (e.g. after a manual `DELETE FROM pricing_rules`), seeding will not run again. Re-seed by deleting `token_inspector.db` and restarting.

## Debugging

When diagnosing unexpected behavior, check the uvicorn console output first — all FastAPI errors print there with full tracebacks.

| What | Where |
|---|---|
| Server errors and tracebacks | uvicorn stdout (the terminal running the server) |
| SQLite data inspection | `sqlite3 token_inspector.db` then `.tables` / `SELECT` |
| API response shape | `curl -s http://localhost:8100/api/<endpoint>` |

## Development Workflow

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server (must be run from tokenInspector/ directory)
python -m uvicorn main:app --host 0.0.0.0 --port 8100

# Start with auto-reload during development
python -m uvicorn main:app --host 0.0.0.0 --port 8100 --reload

# Inspect the database directly
sqlite3 token_inspector.db

# POST a test event
curl -X POST http://localhost:8100/api/events \
  -H "X-Project-Name: test" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","role":"backend","prompt_tokens":1000,"completion_tokens":500,"status":"success"}'

# Reset the database (re-seeds pricing on next start)
rm token_inspector.db
```

## Key File Locations

| What | Where |
|---|---|
| FastAPI app entry + startup seeding | `main.py` |
| SQLite engine + session factory | `database.py` |
| DB models + pricing seed data | `models.py` |
| Ingest endpoint (POST /api/events) | `routes/events.py` |
| Analytics query endpoints | `routes/analytics.py` |
| Pricing CRUD endpoints | `routes/settings.py` |
| SPA shell + nav structure | `static/index.html` |
| All dashboard logic + Chart.js renders | `static/app.js` |
| Dashboard styling | `static/style.css` |
| SQLite database file | `token_inspector.db` (auto-created, gitignored) |

## Recurring Problems

A living record of problems that have recurred or are likely to recur. Check this list when diagnosing unexpected behaviour before diving into code.

1. **`AttributeError: 'AsyncSession' object has no attribute 'exec'` on startup**
   - **Symptoms:** Server crashes immediately after `Waiting for application startup.` with the above error in the traceback.
   - **Root cause:** Route or `main.py` imported `AsyncSession` from `sqlalchemy.ext.asyncio` instead of `sqlmodel.ext.asyncio.session`. SQLAlchemy's `AsyncSession` lacks the `.exec()` method.
   - **Fix/check:** Search for `from sqlalchemy.ext.asyncio import AsyncSession` in all `.py` files and replace with `from sqlmodel.ext.asyncio.session import AsyncSession`.

2. **`estimated_cost_usd` is always `null` for a model**
   - **Symptoms:** POST /api/events returns `{"estimated_cost_usd": null}` even though a pricing rule should exist.
   - **Root cause:** The `model` string in the event payload does not exactly match the `model` primary key in `pricing_rules` (case mismatch or different prefix format).
   - **Fix/check:** `sqlite3 token_inspector.db "SELECT model FROM pricing_rules WHERE model LIKE '%<substring>%';"` to find the exact stored key. Ensure the sending project uses the identical string.
