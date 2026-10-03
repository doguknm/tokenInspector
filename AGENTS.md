# AGENTS.md — Token Inspector Operational Contract

Read this file before changing the backend. Repository files are authoritative; production state must be verified separately.

## Purpose and Boundaries

Token Inspector is a standalone local observability service. It receives structured LLM lifecycle telemetry, stores it in SQLite, calculates cost status, and serves analytics plus a vanilla JavaScript dashboard.

The Hermes producer is a separate standalone plugin repository:

```text
/home/dogukan/Projects/token_inspector
```

Do not modify Hermes core to add telemetry. The producer must remain bounded, short-timeout, asynchronous, fail-open, and privacy-first.

The second producer, the Claude Code hook, lives in this repository under `producers/claude_code/` (stdlib only, runs as a plain script). The same rules apply: bounded (5 s per hook), fail-open (always exit 0, silent), allowlisted metadata only. Installing it changes the global `~/.claude/settings.json` and is a user-approved deploy step.

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
Hermes lifecycle → token_inspector plugin bounded queue ─────────────────────┐
Claude Code Stop/SubagentStop/SessionEnd hook → producers/claude_code (O9) ──┤
  → POST /api/events/batch  ◄────────────────────────────────────────────────┘
  → validation + reserved tag normalization (job_ref, runtime, producer, …) + idempotent insert
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
| `features.py` | feature gates for task prompt capture and JEV, config preflight, Host/Origin allowlists, the `TASK_PROMPT_ALLOWED_PROJECTS` prompt/JEV allowlist |
| `auth.py` | `require_ingest_auth` and `require_sensitive_auth` (token always required, Origin check) |
| `task_store.py` | task derivation shared by the v10 backfill and live ingest (task = Hermes turn); prompt gate (`PROMPT_ELIGIBILITY`, `prompt_root_eligible`: allowlist + proven root, shared with JEV) and the child-transition prompt null |
| `redaction.py` | `task-redact-v1` scrubber, byte-identical to the plugin's `task_redact.py` |
| `retention.py` | purge of expired prompts/notes across DB, WAL and backups; scheduling; status |
| `jev_scorer.py` | JEV worker: durable budget reservations, cooldowns, Retry-After, validation; `project_gate` (allowlist + proven root + completed turn) before every provider attempt |
| `token_inspector_client.py` | safe producer example: bounded queue, batch-only, token, ack validation (`valid_ack`), loss/unconfirmed counters, stable `client_event_id`, no prompt or error text |
| `routes/tasks.py` | Tasks API, labels, evaluate, evaluator status/runs, retention status, purge |
| `routes/meta.py` | `/api/meta`: schema version, feature gates and config errors |
| `jev_pilot.py`, `pilot_metrics.py` | pilot CLI over HTTP (select, blind label, score, report) and its statistics |
| `scripts/purge_task_prompts.py` | stdlib-only purge used by the rollback runbook |
| `scripts/seed_tasks_demo.py` | fixed demo DB for the Tasks view and browser tests |
| `scripts/rollback_v10.sql`, `scripts/check_v9_app_on_v10.py` | schema rollback and code-only rollback check |
| `routes/jobs.py` | `GET /api/jobs`: cost per launcher job (read-only, no auth) |
| `scripts/repair_task_parents.py` | stdlib-only re-link of pre-O9 cross-project child tasks; dry-run default, verified backup before `--apply`, counts only |
| `scripts/rollback_schema.py`, `scripts/rollback_v12.sql`, `scripts/rollback_v11.sql` | the only supported schema rollback (v12 → v11 → v10, one step per transaction): exact source-version guard, SQLite ≥ 3.35 |
| `routes/export.py` | `GET /api/export/v1/{jobs,tasks,events}`: versioned read-only export (snapshot by `ingest_seq`, field and value allowlists, static errors); contract `docs/export-contract-v1.md`, ADR-005 |
| `producers/claude_code/` | stdlib-only Claude Code hook producer: `cc_hook.py` (entry, watchdog, fail-open), `cc_transcript.py` (reader, group finality), `cc_events.py` (allowlisted mapping, `cc-` ids), `cc_attribution.py`, `cc_config.py` (URL, token, aliases, job env), `cc_state.py` (cursor, locks, counters), `cc_client.py` (batch POST, `valid_ack`), `install.py` (settings.json installer) |
| `static/` | dashboard shell, charts, tables, styles (Tasks view included) |
| `tests/` | ingest, idempotency, pricing, analytics, inventory, migration, tasks, retention, JEV, pilot, jobs, repair, Claude Code producer (`test_cc_*.py`, `test_dedup_authority.py`), export (`test_export_*.py`, `test_migration_v12.py`, `test_docs_export.py`) regressions |
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

**Task prompt exception (ADR-002).** `STORE_TASK_PROMPTS` (separate from `STORE_RAW_PROMPTS`, which stays `0`) stores one `task-redact-v1`-scrubbed prompt per task in `tasks.prompt_text`, never in `token_events`. It needs `INGEST_TOKEN` (≥ 16 chars), a valid purge interval and a non-empty `TASK_PROMPT_ALLOWED_PROJECTS`, is never extended past producer capture time + 30 days, and is unreadable once expired even before the purge runs. Activation requires the ADR-002 Approval section.

**Prompt allowlist and root-only rule (O10, ADR-002 §6).**

- One backend list, `TASK_PROMPT_ALLOWED_PROJECTS`, governs prompt storage **and** JEV; the plugin has its own `task_prompt_allowed_projects`. Both must allow a project. Empty = off. Exact names after `strip().lower()`; an entry that does not match `^[a-z0-9][a-z0-9._-]{0,63}$` is dropped.
- A prompt is stored only if capture is enabled, the event carries `prompt_eligibility == "v1-allowed"`, and the **upserted** task row is an allowlisted proven root: `hierarchy_status='root'`, `parent_task_ref IS NULL`, `root_task_ref IS NULL OR root_task_ref = id`. Otherwise the metadata event is still inserted and the prompt is dropped (outcomes `discarded_disabled`, `discarded_no_eligibility`, `discarded_not_eligible`).
- After every task-turn upsert, a row that is `child` and still holds a prompt is NULLed through the purge columns (`prompt_text = NULL`, `prompt_purged_at` set). `prompt_captured_at` stays, so it is never re-stored. An allowlist change alone never purges.
- JEV: `project_gate` runs after the `evaluator_task` skip and again before every provider attempt (ahead of the retention checks), reading the list and the row at call time. An unlisted or non-root task records `project_not_allowed`; a turn that has not ended (`completion` not `session_end`/`next_task` and last seen less than 60 min ago) records `task_not_completed` (`skipped` before the first attempt, `error` after). Requiring a completed turn is what keeps a late child classification from racing a provider send (ADR-002 §6).
- `/api/meta` exposes only `task_prompt_allowlist: configured|empty`. The list, its size, globs and paths never enter events, application logs, `/api/meta` or error responses. Validation errors (single 422 and batch items) never echo input.
- The plugin sends no `error_message`; the backend still accepts it from older producers.

## Tasks (schema v10)

A task is one Hermes turn (`(project_name, session_id, turn_id)`); Hermes' `task_id` is kept as `source_task_id` because it is session-scoped on the gateway. `task_ref = sha256(project \x1f session_id \x1f turn_id)[:32]`. Start complexity uses one rule for backfill and ingest (method `request-shape-v1` from the column or `tags.complexity_version`, earliest `(time, id)` wins). Completion is recomputed per project+session after every event, so arrival order does not matter. Hierarchy comes from `parent_session_id` + `parent_turn_id` (plugin `subagent_start`); `child` is never downgraded. A cross-project child also carries `parent_project_name` (validated by the strict name rule, never stored; invalid → the child's own project), so its `parent_task_ref` is hashed with the parent's project; `scripts/repair_task_parents.py` re-links rows written before that fix.

## Jobs (schema v11, O9 — not deployed yet)

A launcher job is an aggregate over event tags, not a table (ADR-003). The launcher exports `TOKEN_INSPECTOR_JOB_REF` / `_WORK_TYPE` / `_JOB_ATTEMPT`; producers send them as tags `job_ref` / `work_type` / `job_attempt` only when they pass their rules (job ref shape, work type closed list, attempt 1–9999), plus their own `runtime` and `producer`. `_normalize_reserved` (`routes/events.py`) normalizes the reserved keys without ever rejecting an event: invalid `job_ref`/`job_attempt` dropped, unknown `work_type` → `other`, and an invalid or unpaired `runtime`/`producer` drops both and sets `attribution_invalid: true` (ADR-004 pairing table). `tasks.job_ref` holds the first valid job of the task; a later different one increments `tasks.job_ref_conflicts` (conflicting events: calls and tool events) and is never applied; the export counts conflicting counted calls. The job of an event is its task's job, else its own tag. `job_attempt` is the launcher retry, never the per-call `attempt`.

### What the numbers mean (consumer contract)

- **Coverage:** in production today the only producer is the Hermes plugin (runtime `hermes-agent`); totals there are hermes-agent totals, not all LLM spend. After the O9 deploy there are two counting producers, one per runtime (ADR-004): `hermes-plugin` (`hermes-agent`) and `claude-code-hook` (`claude-code@windows`; `claude-code@hermes`, which includes `claude -p` hand-off runs started by `hermes.sh devir-baslat` and interactive Claude Code sessions on hermes — the latter without a `job_ref`). Not measured: application provider calls (`app` runtime, no producer built), and Claude Code calls that leave no usage record (API errors, an interrupted last call of a session that never continues). The Claude Code hook is not installed until that deploy.
- **Jobs:** `GET /api/jobs` counts `llm_request` events only, excluding evaluator (JEV) usage and `attribution_invalid` events. `cost_usd` sums priced calls and is `null` when none is priced; `unpriced_count > 0` means incomplete. A task belongs to at most one job (first job wins); conflicts are reported as calls (`conflict_count`) and affected tasks (`conflict_task_count`). Filters select whole jobs; `days` selects jobs by their last counted event, sums cover the whole job. There is no job-duration event: `first_event_at`/`last_event_at` are not end-to-end job time.
- **Task totals** come from that task's own `llm_request` events only. A root task excludes its children, so summing tasks counts each call once. A task row and its calls must never be added together.
- **Cost:** `cost_usd` sums priced calls only and is `null` when none is priced. `unpriced_count > 0` means the total is incomplete; it is never zero or free. `estimated_cost_usd` holds partial, estimated and legacy costs separately. For subscription providers the dollar value is a list-price estimate, not a bill or a quota.
- **Wall time** is last event − first event of the task. It is not queue time and not end-to-end job time; a launcher measures those.
- **Completion** (`next_task` / `session_end` / `inferred` / `open`) says the turn ended, not that it succeeded.
- **Evaluator usage** (JEV) is stored under project `token-inspector` with `task_id` NULL. It is never a task and must be excluded from task totals.
- **Tool names** in `request_tool_names_json` are the tools offered to the model, not the tools it ran.

## Idempotency and Concurrency

- `client_event_id` is unique within `project_name` when non-null. Ids starting with `cc-` (reserved for the Claude Code producer) are unique across projects (`ux_token_events_cc_client_event`, v11); a conflict on either index is a duplicate, and the first arrival owns project and task (ADR-004).
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

The Claude Code producer (`cc_attribution.py`) resolves the hook's `cwd` locally with: configured alias (longest root prefix) → sanitized Git origin slug → Git root name → `claude-code`; a name equal to the hostname/FQDN or shaped like an IPv4 address is skipped. Parity with the plugin is pinned by `tests/fixtures/attribution_vectors.json` (byte-identical in both repos).

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
GET /api/analytics/complexity-matrix   (days, project, model (repeatable), method=request-shape-v1)

GET/POST/DELETE /api/settings/pricing
GET/POST/DELETE /api/settings/aliases
GET /api/settings/unpriced-models
POST /api/settings/recost

GET  /api/meta
GET  /api/tasks                       (allowed_only=true needs require_sensitive_auth)
GET  /api/tasks/{task_ref}            (include_prompt=true needs require_sensitive_auth)
POST /api/tasks/{task_ref}/labels     (sensitive)
POST /api/tasks/evaluate              (sensitive)
GET  /api/tasks/evaluator-status
GET  /api/tasks/evaluator-runs/{run_id}
GET  /api/tasks/retention-status
POST /api/tasks/purge-expired         (sensitive; dry_run=true by default)

GET  /api/jobs                        (days 1–3650 = 30, runtime, work_type, page, page_size ≤ 200; invalid filter → 400 {"error":"invalid_filter"})

GET  /api/export/v1/jobs|tasks|events (from, to: ISO-8601 with zone, [from, to) ≤ 92 days; limit 1–1000 = 500; cursor; no auth, read-only)
```

The export is a separate contract with its own `schema_version` (1): `docs/export-contract-v1.md`. Errors are static (`invalid_range`, `invalid_limit`, `invalid_cursor` 400; `snapshot_expired` 409; `busy` 503 + `Retry-After: 5`). Pages are a snapshot: `ingest_seq <= as_of` (from `export_state.last_seq` on page 1), and membership and sums — including a task's job — come from snapshot events only. Tasks and jobs are a start-time cohort (first call in the period; sums over all their snapshot calls).

Every `by-*` grouping, `timeseries` and `project-inventory` row carries per-category sums `prompt_tokens`, `completion_tokens`, `cache_read_tokens`, `cache_creation_tokens`. `prompt_tokens` never includes cache (ingest subtracts it when `input_tokens_include_cache` is set). The older `total_tokens` key stays input + output only; the dashboard labels its all-type total as a sum computed from the four fields.

`GET /api/analytics/complexity-matrix` groups `llm_request` events by the per-call `complexity` tier (method from `complexity_method`, else the `complexity_method`/`complexity_version` tag; default `request-shape-v1`, never JEV) and model. Response: `method`, `tier_source` (`llm_call`), `low_sample_tasks` (5), filter options `methods`/`projects`/`models` (each ignores its own filter), `tiers[]` and `cells[]` (`complexity`, `model`). Each tier/cell has `calls`, `tasks` (distinct project+session+turn), `calls_without_task`, the four token sums, `per_call` and `per_task` token dicts (per task uses only task-attributed calls), `avg_process_time_ms`, `avg_ttft_ms`, `error_count`, `error_rate` (status ≠ success), `priced_calls`/`unpriced_calls`/`estimated_calls`, `priced_cost_usd`, `avg_priced_cost_per_call`, `avg_priced_cost_per_task`, `priced_cost_per_1k_output` (all cost fields null when nothing is priced), `cost_complete`, `completion` counts (`session_end`/`next_task`/`inferred`/`open`/`unknown`) and `low_sample` (tasks < 5); cells also carry `lowest_cost_per_task`/`lowest_latency` among non-low-sample models in the tier (cost only when `cost_complete`, at least two candidates). A task with calls in several tiers or models counts once in each.

Ingest auth is optional and controlled by deployment configuration. If enabled, producers use the matching secret environment variable; never place it in repository config or docs.

## Development and Verification

```bash
cd /home/dogukan/Projects/tokenInspector
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile *.py routes/*.py scripts/*.py producers/claude_code/*.py
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
- `TrustedHostMiddleware` rejects any Host not in `TOKEN_INSPECTOR_ALLOWED_HOSTS` (default `127.0.0.1,localhost`); test clients use `http://test`, so the test conftest adds `test`. Production serves the dashboard to the tailnet through `tailscale serve` (`https://<tailnet-host>` → `127.0.0.1:8100`), so that host and origin are allowed in the `remote-access.conf` systemd drop-in (README → Run). The real tailnet name lives only in that drop-in, never in repository docs.
- Retention globs `<db>.bak-v*` but skips `-wal`/`-shm`/`-journal` siblings, which are not databases.
- The lifespan ends with `engine.dispose()`: aiosqlite worker threads are non-daemon, so an undisposed pooled connection blocks process exit.
- Distro SQLite builds (hermes) default `secure_delete` ON; tests that need leftover freed-page bytes set it OFF explicitly.
- Never normalize allowlist entries with the plugin's `normalize_project_name`: it falls back to `hermes` on garbage, so a typo would silently allowlist the `hermes` fallback. Both repos drop invalid entries instead.
- **Cross-project child (Q1, fixed in O9):** `_upsert_task` hashes the parent with the validated `parent_project_name` when present (else the child's own project), and the re-root update is no longer limited to one project. Rows written before the fix keep a dangling `parent_task_ref` until `scripts/repair_task_parents.py --apply` runs (dry-run first; `--apply` only with user approval on prod).
- Tags are capped at `TAG_BYTES_MAX` = 1024 bytes and 20 keys (`routes/events.py`). The worst-case plugin tags measure 17 keys / 722 bytes (`tests/fixtures/worst_case_plugin_tags.json`, byte-identical in both repos); measure again before adding a tag key.
- `attribution_invalid` is backend-only: an incoming value is removed before normalization, and the mark is set only when a present `runtime`/`producer` was dropped. Marked events are stored; excluded from jobs and the export (analytics totals are not filtered); and never treated as legacy `hermes-agent`.
- Schema rollback only through `scripts/rollback_schema.py`, never by running a `rollback_v*.sql` by hand.
- Any write that deletes or rewrites stored `token_events` rows (or the task data the export reads) must bump `export_state.revision` in the same transaction, as recost apply and repair apply do; otherwise an open export cursor silently mixes two states of the data.
- `ingest_seq` is assigned only by `_insert_event` (scalar subquery on `export_state.last_seq` + the `last_seq` bump when the row was really inserted). Any other insert path leaves it NULL until the next startup numbers it (self-heal in `database.init_db`); a high-water must never be computed from `MAX(ingest_seq)`.
- `ingest_seq` is internal: it is never returned by `GET /api/events` or the ingest ack.
- The Claude Code hook must end through `sys.exit(main())`, not `os._exit`, or buffered output can be dropped silently; only the watchdog hard-exits. It must stay stdlib-only (`test_cc_producer_is_stdlib_only`).
- Global Claude Code hooks take effect in every running session on that machine immediately, without a restart: run `install.py --apply` only after the backend with the Phase 2 code is live, and `--uninstall --apply` first if anything misbehaves or before a backend rollback below it.
- The child-transition null overwrites the row in place (`secure_delete`), but the WAL frame that held the prompt stays until the next checkpoint (auto-checkpoint or the retention loop's `wal_checkpoint(TRUNCATE)`). Byte-level tests checkpoint first.
- `GET /api/tasks?allowed_only=true` binds the allowlist names as SQL parameters; they show up only in the aiosqlite DEBUG driver log (off in production), never in application logs.
- Prompt tests after O10 must take the allowed path: list the project, send `task_hierarchy="root"` and `prompt_eligibility="v1-allowed"` (backend), or call `_on_session_start` and pass the attach authorization (plugin). JEV test rows must be `root`.
