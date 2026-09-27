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
| `features.py` | feature gates for task prompt capture and JEV, config preflight, Host/Origin allowlists |
| `auth.py` | `require_ingest_auth` and `require_sensitive_auth` (token always required, Origin check) |
| `task_store.py` | task derivation shared by the v10 backfill and live ingest (task = Hermes turn) |
| `redaction.py` | `task-redact-v1` scrubber, byte-identical to the plugin's `task_redact.py` |
| `retention.py` | purge of expired prompts/notes across DB, WAL and backups; scheduling; status |
| `jev_scorer.py` | JEV worker: durable budget reservations, cooldowns, Retry-After, validation |
| `routes/tasks.py` | Tasks API, labels, evaluate, evaluator status/runs, retention status, purge |
| `routes/meta.py` | `/api/meta`: schema version, feature gates and config errors |
| `jev_pilot.py`, `pilot_metrics.py` | pilot CLI over HTTP (select, blind label, score, report) and its statistics |
| `scripts/purge_task_prompts.py` | stdlib-only purge used by the rollback runbook |
| `scripts/seed_tasks_demo.py` | fixed demo DB for the Tasks view and browser tests |
| `scripts/rollback_v10.sql`, `scripts/check_v9_app_on_v10.py` | schema rollback and code-only rollback check |
| `static/` | dashboard shell, charts, tables, styles (Tasks view included) |
| `tests/` | ingest, idempotency, pricing, analytics, inventory, migration, tasks, retention, JEV, pilot regressions |
| `tests/browser/` | Playwright suite (marker `browser`; skipped without Playwright) |

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

**Task prompt exception (ADR-002).** `STORE_TASK_PROMPTS` (separate from `STORE_RAW_PROMPTS`, which stays `0`) stores one `task-redact-v1`-scrubbed prompt per task in `tasks.prompt_text`, never in `token_events`. It needs `INGEST_TOKEN` (≥ 16 chars) and a valid purge interval, is never extended past producer capture time + 30 days, and is unreadable once expired even before the purge runs. Activation requires the ADR-002 Approval section.

## Tasks (schema v10)

A task is one Hermes turn (`(project_name, session_id, turn_id)`); Hermes' `task_id` is kept as `source_task_id` because it is session-scoped on the gateway. `task_ref = sha256(project \x1f session_id \x1f turn_id)[:32]`. Start complexity uses one rule for backfill and ingest (method `request-shape-v1` from the column or `tags.complexity_version`, earliest `(time, id)` wins). Completion is recomputed per project+session after every event, so arrival order does not matter. Hierarchy comes from `parent_session_id` + `parent_turn_id` (plugin `subagent_start`); `child` is never downgraded.

### What the numbers mean (consumer contract)

- **Coverage:** the only producer today is the Hermes plugin (runtime `hermes-agent`). Not measured: Claude Code on Windows, `claude -p` hand-off runs started by `hermes.sh` on hermes (a separate runtime the plugin does not see), and application provider calls. Totals are hermes-agent totals, not all LLM spend.
- **Task totals** come from that task's own `llm_request` events only. A root task excludes its children, so summing tasks counts each call once. A task row and its calls must never be added together.
- **Cost:** `cost_usd` sums priced calls only and is `null` when none is priced. `unpriced_count > 0` means the total is incomplete; it is never zero or free. `estimated_cost_usd` holds partial, estimated and legacy costs separately. For subscription providers the dollar value is a list-price estimate, not a bill or a quota.
- **Wall time** is last event − first event of the task. It is not queue time and not end-to-end job time; a launcher measures those.
- **Completion** (`next_task` / `session_end` / `inferred` / `open`) says the turn ended, not that it succeeded.
- **Evaluator usage** (JEV) is stored under project `token-inspector` with `task_id` NULL. It is never a task and must be excluded from task totals.
- **Tool names** in `request_tool_names_json` are the tools offered to the model, not the tools it ran.

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

**JEV** (`jev_scorer.py`) is a separate, semantic start-of-task difficulty score stored in `task_evaluations`, never in `token_events.complexity`. It is not execution intensity (tokens, cost, tool calls, wall time), which the Tasks API reports separately. It runs only via `POST /api/tasks/evaluate`.

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

GET  /api/meta
GET  /api/tasks
GET  /api/tasks/{task_ref}            (include_prompt=true needs require_sensitive_auth)
POST /api/tasks/{task_ref}/labels     (sensitive)
POST /api/tasks/evaluate              (sensitive)
GET  /api/tasks/evaluator-status
GET  /api/tasks/evaluator-runs/{run_id}
GET  /api/tasks/retention-status
POST /api/tasks/purge-expired         (sensitive; dry_run=true by default)
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
- Do not enable `STORE_TASK_PROMPTS` or `JEV_ENABLED` in production before the ADR-002 Approval section is filled in.
- Never log, return or store `AI_GATEWAY_API_KEY`; it lives only in `~/.config/token-inspector/secrets.env` (mode 600).

## Gotchas

- `SQLModel.metadata.create_all` runs before migrations, so the model classes define new tables; `_m10` creates them with `checkfirst` and adds `token_events` columns in declaration order so fresh and migrated schemas agree.
- The migration engine emits `BEGIN IMMEDIATE`; without it pysqlite autocommits DDL and a failed migration leaves partial tables.
- `TrustedHostMiddleware` rejects any Host not in `TOKEN_INSPECTOR_ALLOWED_HOSTS` (default `127.0.0.1,localhost`); test clients use `http://test`, so the test conftest adds `test`. Production serves the dashboard to the tailnet through `tailscale serve` (`https://hermes.tail3a755d.ts.net` → `127.0.0.1:8100`), so that host and origin are allowed in the `remote-access.conf` systemd drop-in (README → Run).
- Retention globs `<db>.bak-v*` but skips `-wal`/`-shm`/`-journal` siblings, which are not databases.
- The lifespan ends with `engine.dispose()`: aiosqlite worker threads are non-daemon, so an undisposed pooled connection blocks process exit.
- Distro SQLite builds (hermes) default `secure_delete` ON; tests that need leftover freed-page bytes set it OFF explicitly.
