# Feature Plan: task-telemetry-jev-pilot

**Date:** 2026-09-27
**Status:** planned

## Summary
This feature adds task-level telemetry to tokenInspector: a `tasks` / `task_evaluations` schema, redacted per-task prompts retained for 30 days, and a budget-safe JEV difficulty scorer. It also fixes the Hermes producer plugin: field mapping and a durable, scrubbed spool. A blind human-labelled pilot of about 50 tasks then compares JEV, the human labels and request-shape-v1.

## Problem
tokenInspector records per-call token events but has no notion of a task (one user prompt through the final stop). So difficulty cannot be compared with cost and intensity.

The Hermes plugin has two defects:
- It leaves `response_size_bytes`, `tool_call_count` and `role` empty on every production row, because Hermes core renamed those fields.
- It silently drops batches that fail to send.

The only complexity signal today is the deterministic request-shape-v1 heuristic. JEV (typesafe-ai via the Vercel AI Gateway) is a cheap semantic difficulty scorer, but it is unvalidated.

Judging start-of-task difficulty needs the prompt text. That reverses the documented `STORE_RAW_PROMPTS=0` privacy default, so it must be redacted, retained for a bounded time, approved and documented. Production holds 9,146 events (8,850 with `task_id`) and zero stored prompts, so historical tasks can only give metadata; the pilot must use tasks collected prospectively.

## Architecture decisions
- **Database (schema v10, additive, same SQLite DB):**
  - New tables: `tasks`, `task_evaluations`, `evaluator_runs`, `evaluator_attempts`, `provider_cooldowns`, `retention_state`.
  - New nullable `token_events` columns: request-composition counts and `complexity_method`.
  - The migration runs in one explicit transaction on a dedicated migration engine (`BEGIN IMMEDIATE`).
  - It is preceded by an integrity-verified SQLite online-backup-API backup.
  - `PRAGMA secure_delete=ON`.
- **Task identity:** `task_ref = sha256(project_name‖task_id)[:32]`, which is deterministic, so backfill and ingest agree.
- **Task derivation:**
  - Historical tasks are backfilled metadata-only, with hierarchy `unknown`.
  - Live ingest upserts task rows.
  - Start complexity uses one approved-provenance candidate rule (`request-shape-v1`) plus `(time, event id)` ordering in both paths.
  - Completion (`session_end` / `next_task` / `inferred` after 60 minutes / `open`) is recomputed per project+session, so it does not depend on arrival order.
- **Privacy and retention:**
  - A separate `STORE_TASK_PROMPTS` flag controls capture; `STORE_RAW_PROMPTS` stays 0.
  - A shared pattern scrubber `task-redact-v1` runs in both the plugin (before queueing or spooling) and the backend.
  - Retention is capture-time based: 30 days from producer capture, never extended, and enforced at every read.
  - The purge nulls the column, then runs `wal_checkpoint(TRUNCATE)` with a persisted retry, and re-purges + VACUUMs the DB-directory backups.
  - Retention stays scheduled whenever retained data exists.
  - A standalone `scripts/purge_task_prompts.py` exists for rollback.
  - ADR-002 documents logical vs physical deletion and provider-side limits.
- **Security:**
  - `INGEST_TOKEN` is mandatory for capture and JEV; without it those features refuse to start.
  - `require_sensitive_auth` protects the prompt, label, evaluate and purge endpoints.
  - A global Host allowlist and an Origin check are added.
  - A three-phase Activation Gate applies: (a) approval + configuration preflight with the flags off, (b) controlled activation, (c) post-start verification.
- **JEV worker:**
  - Opt-in, and triggered only via `POST /api/tasks/evaluate`.
  - Atomic run reservation, with one active run enforced by a unique index.
  - Every HTTP call is preceded by a durable budget reservation using an upper-bound token estimate, and then reconciled to the gateway-reported cost.
  - typesafe-ai goes first, with a digitalocean fallback on 429 only.
  - `Retry-After` is honored exactly, through persisted per-provider cooldowns.
  - Responses are strictly validated against the real captured fixture.
  - Evaluator usage is recorded as `token-inspector` events with no `task_id`, so it is never scored.
- **API:**
  - `GET /api/meta` (schema version, feature flags, `config_errors`);
  - `GET /api/tasks` (stable order; `projects` ignores all filters except `days`);
  - `GET /api/tasks/{ref}` (documented `children[]` shape);
  - `evaluator-status`, `evaluator-runs/{id}`, `retention-status`;
  - `POST` labels (label or persisted skip), evaluate, and purge-expired.
- **Dashboard:** a vanilla-JS Tasks view with a table, detail panel and scatter chart capped at the latest 200 with a label. It has stale-response generation counters (Close invalidates in-flight requests), defined empty, out-of-range and chart-error states, and escaped output, and it shows no prompt text.
- **Hermes plugin (hermes-only repo):**
  - Field mapping fix.
  - Durable spool with fsync, a dead-letter folder, capture-time prompt expiry, and orphan `.tmp` recovery.
  - Capture fields sent only after `GET /api/meta` reports schema ≥ 10 with capture on.
- **Pilot:**
  - Stratified seeded sampling of completed root tasks.
  - A blind label CLI that escapes terminal control characters, with persisted per-labeler progress.
  - A report on a paired complete-case cohort (at least 40 pairs). The outcome is PASS or FAIL against pre-registered thresholds, or `INCONCLUSIVE`.

## Planned file changes
**tokenInspector (this repo; canonical copy on hermes):**
- **Modify:**
  - `migrations.py` (`_m10`, online backup + verification, migration engine)
  - `database.py` (`secure_delete`)
  - `models.py` (6 new models, new `TokenEvent` columns/index, `typesafe-ai/jev` pricing seed)
  - `main.py` (lifespan purge/recovery, Host middleware, router)
  - `auth.py` (`require_sensitive_auth`)
  - `routes/events.py` (new fields, task upsert, completion recompute)
  - `static/index.html`, `static/app.js`, `static/style.css` (Tasks view)
  - `tests/conftest.py`
- **New:**
  - `routes/tasks.py`, `task_store.py`, `redaction.py`, `retention.py`, `jev_scorer.py`, `jev_pilot.py`, `pilot_metrics.py`
  - `scripts/purge_task_prompts.py`, `scripts/seed_tasks_demo.py`
  - `tests/fixtures/` (`schema_v9.sql`, `redaction_vectors.json`, `jev_response_2026-09-27.json`)
  - new backend test modules and `tests/browser/` (Playwright)
- **Docs:**
  - new `docs/adr/002-task-prompt-retention-and-jev.md`
  - updates to `AGENTS.md`, `README.md`, `CHANGELOG.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md` and `CLAUDE.md`
  - `.github/workflows/ci.yml` (scripts compile + Playwright job)
  - `requirements-dev.txt` (`pytest-playwright`)

**Hermes plugin (`/home/dogukan/Projects/token_inspector`, hermes only, tarball backup first):**
- `mapping.py`, `redact.py` (`scrub_text` port), `sink.py`, `state.py`, `__init__.py` (hooks: prompt capture, hierarchy, session end, composition, meta probe), `config.py` (`capture_task_prompt`), plus tests and fixtures.

**Plan artifacts:**
- `Plans/task-telemetry-jev-pilot/discovery-evidence.md` (D0, to be produced)
- `pilot-report.md` (after the pilot)

## Open questions
All brief open questions were resolved in planning and in plan-review rounds 1–2. The remaining operational items are tracked in `status.md` → Open Items, each with an owner, an evidence artifact and what it blocks:
- **O1–O4:** D0 discovery on hermes (`task_id` scope, hook fields, historical complexity provenance, tasks/day and pilot start date).
- **O5:** the plugin implementation route.
- **O6:** the canonical backend commit/sync workflow (GitHub is 4 commits behind hermes).
- **O7:** the service-unit environment wiring.
- **O8:** the user's ADR-002 approval (Activation Gate phase a).
