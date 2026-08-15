# CLAUDE.md

Guidance for AI coding assistants working on Token Inspector.

## AI Tools

NOTEBOOKLM_NOTEBOOK_ID: 873a6470-05c2-40e1-be01-54b697f283aa

## Project Overview

Token Inspector is a standalone, privacy-first FastAPI service for LLM token observability. Connected producers send structured lifecycle events; the service performs idempotent ingest, pricing, SQLite persistence, and analytics for a vanilla JavaScript dashboard.

The Hermes integration is intentionally separate at `/home/dogukan/Projects/token_inspector`. Do not embed it into Hermes core.

## Read First

1. `AGENTS.md` — operational contract and module map.
2. `ARCHITECTURE.md` — boundaries, data flow, identity, privacy, and state.
3. `PROJECT_MEMORY.md` — current verified state, durable decisions, and open items.

## Production Runtime

```text
Service: systemd user unit token-inspector.service
Bind: 127.0.0.1:8100
Database: /home/dogukan/.local/share/token-inspector/token-inspector.db
Dashboard: http://127.0.0.1:8100/
```

Production uses `DB_PATH` explicitly. Never assume the repository-local default database is production. Do not commit, mirror, or upload SQLite files or backups.

## Critical Technical Patterns

- Raw prompts, responses, and tool arguments are off by default.
- `STORE_RAW_PROMPTS=0` in production.
- Prompt payload is accepted only as an explicit opt-in and is capped at 3000 characters.
- Ingest remains fail-open for producers but validation remains strict at the backend.
- `client_event_id` is idempotent per project using a partial unique SQLite index.
- SQLite requires WAL, `busy_timeout`, foreign keys, and additive migrations.
- Cache-read, cache-creation, reasoning, prompt, and completion tokens remain distinct.
- Requested model, resolved model, and pricing model remain distinct.
- Unknown pricing is `unpriced/no_rule`; never represent it as free.
- Deterministic complexity uses `request-shape-v1` numeric metadata, not raw prompt semantics.
- Filesystem repository inventory and event-backed observed activity are different datasets.
- Absolute workspace paths and complete Git remote URLs must not enter telemetry storage.

## Project Identity

The dashboard exposes:

1. **Known Repositories** — bounded discovery under configured roots, including repositories with zero events.
2. **Observed Activity** — aggregation of `TokenEvent.project_name` for actual LLM events.

Canonical producer attribution order is maintained in the standalone Hermes plugin:

```text
hook metadata
→ optional explicit marker (disabled by default)
→ configured path alias
→ sanitized Git origin repository slug
→ Git root directory name
→ hermes fallback
```

The backend inventory defaults to direct Git children of `~/Projects`, is bounded by `TOKEN_INSPECTOR_MAX_PROJECTS`, and returns only canonical name, directory name, discovery source, and a hash-first workspace identifier.

## Debugging

Start with `systemctl --user status token-inspector.service`, the backend journal, the relevant API response, and scoped SQLite metadata. For project anomalies trace `session_id`, `trace_id`, timestamp, `project_source`, and `project_confidence`; never inspect or copy raw content as a first diagnostic step.

## Development Workflow

```bash
cd /home/dogukan/Projects/tokenInspector
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile *.py routes/*.py
node --check static/app.js

DB_PATH=/tmp/token-inspector-dev.db STORE_RAW_PROMPTS=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8100
```

Never delete the production database to re-seed pricing. Use settings APIs, model aliases, and recost dry-run/apply operations.

## Key File Locations

| Concern | Path |
|---|---|
| App startup and routers | `main.py` |
| SQLite engine/migrations | `database.py`, `migrations.py` |
| Event/pricing schema | `models.py` |
| Ingest/privacy/idempotency | `routes/events.py` |
| Analytics/inventory join | `routes/analytics.py`, `project_inventory.py` |
| Pricing aliases/recost | `routes/settings.py` |
| Legacy opt-in AI scorer | `complexity_scorer.py` |
| Dashboard | `static/index.html`, `static/app.js`, `static/style.css` |
| Tests | `tests/` |
| Hermes producer plugin | `/home/dogukan/Projects/token_inspector` |

## Recurring Problems

1. **Phantom project attribution**
   - Check event `session_id`, `trace_id`, timestamp, and tags `project_source`/`project_confidence`.
   - Do not infer activity from filesystem presence.
   - Prompt markers must remain disabled unless explicitly opted in.

2. **Repository folder differs from canonical repository name**
   - Prefer configured alias, then sanitized Git `origin` slug, then root folder name.
   - Never store or display the full remote URL.

3. **Unknown model appears as zero cost**
   - Check `cost_status`, `cost_status_reason`, and `pricing_model`.
   - Add a pricing rule or alias and use recost with dry-run first.

4. **Unexpected or empty database**
   - Inspect the service `DB_PATH`; repository-local `token_inspector.db` is not production.

5. **Backend unavailable**
   - Producer plugin must remain bounded and fail-open; inspect its queue/flush behavior without blocking Hermes.
