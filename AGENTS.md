# AGENTS.md — Token Inspector Operational Contract

Read this file before changing the backend. Repository files are authoritative; production state must be verified separately.

## Purpose and Boundaries

Token Inspector is a standalone local observability service. It receives structured LLM lifecycle telemetry, stores it in SQLite, calculates cost status, and serves analytics plus a vanilla JavaScript dashboard.

The Hermes producer is a separate standalone plugin repository:

```text
/home/dogukan/Projects/token_inspector
```

Do not modify Hermes core to add telemetry. The producer must remain bounded, short-timeout, asynchronous, fail-open, and privacy-first.

## Production

```text
FastAPI bind: 127.0.0.1:8100
Systemd user unit: token-inspector.service
Database: /home/dogukan/.local/share/token-inspector/token-inspector.db
Raw prompt storage: disabled
```

The service unit sets `DB_PATH`; the repository-relative default exists only for development compatibility. Database files and backups stay outside Git and outside Vault/NotebookLM.

## Architecture

```text
Hermes lifecycle
  → token_inspector plugin bounded queue
  → POST /api/events/batch
  → validation + normalization + idempotent insert
  → pricing rule/alias resolution
  → SQLite WAL database
  → analytics API
  → dashboard
```

Backend unavailability must never break the caller. Backend validation failures must remain visible through rejected counts and HTTP responses.

## Module Map

| File | Responsibility |
|---|---|
| `main.py` | FastAPI lifecycle, table initialization, router/static registration |
| `database.py` | Async engine/session, SQLite pragmas, migration startup |
| `migrations.py` | Additive schema migration and pre-migration backup |
| `models.py` | `TokenEvent`, `PricingRule`, `ModelAlias`, indexes and seed pricing |
| `routes/events.py` | auth, validation, privacy normalization, single/batch ingest, event listing |
| `routes/analytics.py` | SQL aggregation, project filters, complexity, routing, inventory join |
| `project_inventory.py` | bounded direct-child Git discovery and privacy-safe canonical identity |
| `routes/settings.py` | pricing CRUD, aliases, unpriced models, recost dry-run/apply |
| `complexity_scorer.py` | legacy opt-in raw-prompt AI scoring only |
| `static/` | dashboard shell, charts, tables, styles |
| `tests/` | ingest, idempotency, pricing, analytics, inventory regressions |

## Data and Privacy Contract

`TokenEvent` supports:

- provider, event type, requested/resolved/pricing model;
- session, task, turn, trace/span, API request, tool and role correlation;
- prompt, completion, cache-read, cache-creation, reasoning and total token fields;
- status, HTTP/error metadata, attempt/retry, TTFT and latency;
- priced/estimated/unpriced cost status and pricing version;
- prompt hash/length and optional capped prompt text;
- deterministic complexity and structured tags.

Defaults:

```text
STORE_RAW_PROMPTS=0
capture_content=false
capture_tool_args=false
```

Never persist raw response bodies or tool arguments. Never send absolute workspace paths, complete remotes, credentials, private network identifiers, or authenticated payloads.

## Idempotency and Concurrency

- `client_event_id` is unique within `project_name` when non-null.
- SQLite 3.24+ is required for atomic conflict handling.
- WAL, `busy_timeout=5000`, foreign keys, and `synchronous=NORMAL` are required.
- Schema changes are additive migrations; do not replace the production DB.
- Run recost with `dry_run=true` before applying.

## Pricing

Keep these identities separate:

```text
model_requested → resolved model → model alias → pricing_model
```

Cost status meanings:

- `priced`: complete rule applied.
- `estimated`/`partial`/`legacy`: non-final approximation.
- `unpriced`: no valid rule; cost remains null, not zero/free.

Cache pricing has independent rates. Reasoning tokens may already be represented in provider output usage and must not be double-charged.

## Complexity

Primary path:

```text
request-shape-v1
```

It uses numeric request metadata: current user-message length, message count, and approximate input token count. It does not claim semantic difficulty and does not require raw prompt storage. Analytics includes unpriced observations in token averages; cost averages remain null without priced observations.

`complexity_scorer.py` is legacy and only runs when a source explicitly submits `prompt_text` without complexity and credentials are configured.

## Project Identity and Inventory

Do not conflate repository discovery with telemetry activity.

### Known Repositories

`GET /api/analytics/project-inventory` scans only direct Git children of configured roots. Defaults:

```text
TOKEN_INSPECTOR_PROJECT_ROOTS=~/Projects
TOKEN_INSPECTOR_MAX_PROJECTS=200
```

Canonical identity:

```text
sanitized Git origin slug → Git root directory name
```

Response paths are represented by a short SHA-256-derived `workspace_id`; absolute paths and remote URLs are not returned.

### Observed Activity

`GET /api/analytics/by-project` groups actual LLM events by `TokenEvent.project_name`. A known repository with zero events must display `inventory only`, not observed activity.

Producer attribution is implemented by the plugin with this order:

```text
hook metadata
→ optional marker (disabled by default)
→ configured path alias
→ sanitized Git origin slug
→ Git root name
→ hermes fallback
```

If a phantom project appears, trace it by `session_id`, `trace_id`, timestamp, `project_source`, and `project_confidence` before changing code or data.

## API Families

```text
POST /api/events
POST /api/events/batch
GET  /api/events

GET /api/analytics/summary
GET /api/analytics/project-inventory
GET /api/analytics/by-project
GET /api/analytics/by-model
GET /api/analytics/by-provider
GET /api/analytics/by-tool
GET /api/analytics/by-role
GET /api/analytics/by-session
GET /api/analytics/timeseries
GET /api/analytics/by-complexity
GET /api/analytics/routing-recommendations
GET /api/analytics/by-role-model-complexity

GET/POST/DELETE /api/settings/pricing
GET/POST/DELETE /api/settings/aliases
GET /api/settings/unpriced-models
POST /api/settings/recost
```

Ingest auth is optional and controlled by deployment configuration. If enabled, producers use the matching secret environment variable; never place it in repository config or docs.

## Development and Verification

```bash
cd /home/dogukan/Projects/tokenInspector
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile *.py routes/*.py
node --check static/app.js
git diff --check
```

Production verification:

```bash
systemctl --user is-active token-inspector.service
curl -fsS http://127.0.0.1:8100/api/analytics/summary?days=1
curl -fsS http://127.0.0.1:8100/api/analytics/project-inventory?days=90
```

Restarting the backend is separate from restarting Hermes. A plugin reinstall requires `hermes gateway restart` from an external shell.

## Safety Rules

- Do not delete or reset production data to fix pricing or schema problems.
- Back up SQLite online before scoped corrective deletion.
- Do not treat a directory name as canonical when a sanitized Git remote slug exists.
- Do not enable prompt marker attribution by default.
- Do not claim NotebookLM sync without source read-back.
- Do not mirror SQLite, `.env`, service secrets, raw logs, or session transcripts.
