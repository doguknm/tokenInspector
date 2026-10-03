# Changelog

All notable user-visible and operational changes are documented here.

## Unreleased

### Deployed

- 2026-10-03: O9 in production on hermes (backend schema v12, plugin with job tags, Claude Code hook on Windows and hermes, the read-only export v1). Known gap: `hermes -z` jobs lose their last ~2 s of events at exit.

### Added

- Schema v13 data layer (not an Agents dashboard tab): Claude Code emits privacy-gated per-run agent labels
  and the separate `cc-input-size-v1` call tier; events store `agent`, tasks derive agent/conflict/missing and
  method-qualified start complexity, export v1 adds snapshot-derived fields, and `GET /api/analytics/agents`
  provides a bounded read contract. Coverage begins at deployment; named labels are not guaranteed.

- Complexity view: tier × model comparison (calls, tasks, per-call and per-task token split, latency, TTFT, error rate, priced cost per task, completion counts, low-sample marks) with project/model/method/day filters, backed by the new `GET /api/analytics/complexity-matrix`; the demo seed now carries per-call tiers and two priced models.
- Task telemetry (schema v10): one task per Hermes turn, derived at ingest and backfilled metadata-only, with deterministic start complexity, arrival-order-independent completion and hierarchy from subagent starts.
- Tasks view and Tasks API (list, detail, blind labels), `/api/meta` feature gates and config preflight.
- Gated, scrubbed task prompt storage (`STORE_TASK_PROMPTS`) with 30-day retention from producer capture time across DB, WAL and backups (ADR-002).
- JEV difficulty worker via Vercel AI Gateway with durable per-call budget reservations, persisted provider cooldowns and exact Retry-After handling.
- JEV pilot CLI (`jev_pilot.py`) with a paired-cohort report and pre-registered thresholds.
- Request-composition counts on LLM events; `scripts/purge_task_prompts.py`, `scripts/seed_tasks_demo.py`, `scripts/rollback_v10.sql`.
- Per-project prompt/JEV allowlist `TASK_PROMPT_ALLOWED_PROJECTS` (default empty = off): prompts are stored and scored only for proven root tasks of listed projects that carry the plugin's `prompt_eligibility` marker; a task that becomes a child loses its stored prompt. `/api/meta` reports `task_prompt_allowlist` (`configured`/`empty`) and `no_allowed_projects`.
- JEV skips tasks of unlisted projects or non-root tasks with `project_not_allowed`, and turns that have not ended with `task_not_completed`, checked before every provider attempt and before the retention checks; no automatic purge.
- `GET /api/tasks?allowed_only=true` (token required); `jev_pilot.py select` uses it.
- Plugin: `task_prompt_allowed_projects` and `task_prompt_deny_path_globs` (default `~/Projects/*-devir`); prompts only for root sessions, stripped from queue and spool when a session turns out to be a subagent.
- Job correlation (O9, schema v11): launcher env contract `TOKEN_INSPECTOR_JOB_REF` / `_WORK_TYPE` / `_JOB_ATTEMPT` (the `/hermes` skill passes `HERMES_WORK_TYPE` / `HERMES_JOB_ATTEMPT` to `hermes.sh`), reserved tag keys `job_ref`, `runtime`, `work_type`, `job_attempt`, `producer` normalized at ingest without rejecting events, `attribution_invalid` mark, `tasks.job_ref` (first job wins) and `tasks.job_ref_conflicts` (ADR-003).
- `GET /api/jobs` and a dashboard **Jobs** view: cost per launcher job from priced calls only (unpriced count shown, never `$0`), JEV usage and invalid attribution excluded, conflicts as calls and tasks, runtime/work-type/day filters that select whole jobs.
- `scripts/repair_task_parents.py`: re-links pre-O9 cross-project child tasks (dry-run default, counts only; `--apply` takes a verified online backup first).
- `scripts/rollback_schema.py` (+ `scripts/rollback_v11.sql`): the only supported schema rollback, with an exact source-version guard.
- Claude Code producer `producers/claude_code/` (O9): stdlib-only `Stop`/`SubagentStop`/`SessionEnd` hook sending one event per API call with allowlisted metadata only (never text or paths), 5 s bound, fail-open, at-least-once with idempotent `cc-` ids; subagents as child tasks; `install.py` with a content-free dry-run summary, exact-command ownership, backup, atomic write and the PossibleSkills settings lock (ADR-004).
- Plugin: reads the launcher job env once per process and sends validated `job_ref` / `work_type` / `job_attempt` plus `runtime=hermes-agent` / `producer=hermes-plugin` on its events, and `parent_project_name` on child events.
- Versioned read-only export v1 (O9 Phase 3, schema v12): `GET /api/export/v1/{jobs,tasks,events}` with a JSON envelope (`schema_version`, period, `fields`, staleness, coverage), keyset pagination over a snapshot, start-time cohorts for tasks and jobs, field and value allowlists, static errors (`invalid_range`, `invalid_limit`, `invalid_cursor`, `snapshot_expired`, `busy`). Contract `docs/export-contract-v1.md`, ADR-005.
- Jobs view: an **Export (v1)** panel that downloads jobs, tasks or LLM calls as CSV built in the browser from the JSON export (same fields, formula-injection guard, 100 000-row cap) and links the JSON first page.
- Schema v12: `token_events.ingest_seq` assigned in commit order from the persistent high-water `export_state.last_seq`, plus `export_state.revision`/`epoch` for cursor invalidation; rows written by older code are numbered at startup. `scripts/rollback_schema.py` gains the v12 → v11 step (`scripts/rollback_v12.sql`).

### Changed

- Recost with `dry_run=false` and changes, and `scripts/repair_task_parents.py --apply` with changes now also bump `export_state.revision`, which expires open export cursors.
- `/api/meta` reports `schema_version` 12.
- Test: the concurrent writer in `test_repair_backup_accepts_real_concurrent_writer` pauses 1 ms between commits, so it no longer starves the repair's write lock on hermes (Recurring Problem 19).

- Docs translated to English: the last Turkish references in the O9 plan (`Plans/o9-job-correlation-cc-producer/`) and the project's Claude memory.
- `token_inspector_client.py` and the README `push_token_event` helper are a safe producer example: token, batch-only, status and ack checks, loss/unconfirmed counters, stable `client_event_id`, no prompt or error text.
- The Hermes plugin no longer sends `error_message`; it sends an identifier-shaped `error_type` and `http_status`. The backend still accepts `error_message` from older producers.
- Validation errors (single 422 and batch items) no longer echo the rejected input.
- Safe client: events are validated at enqueue with the transport's JSON encoding (NaN/Infinity and invalid UTF-8 count as `serialization_failed`); the README "Batch ingest" example counts rejected events as lost and malformed acks as unconfirmed.
- Event tags may encode to 1024 bytes (was 512); the 20-key limit is unchanged.
- `client_event_id` values starting with `cc-` are reserved for the Claude Code producer and unique across projects (a resumed copy in another project is a duplicate); a `runtime` must come with its paired `producer` or the event is marked `attribution_invalid` and not counted in jobs.
- Plugin: late-child revocation is re-checked before every HTTP call and before a failed batch is spooled or dead-lettered; revoked sessions are kept for the process lifetime and an overflow strips every prompt (fail-closed); a path-denied session loses its root evidence; `error_message` is removed from every outgoing event, legacy spool replay included.

### Fixed

- O9 code review r1: `/api/jobs` re-applies the job, runtime and work-type value rules to legacy tags; the export answers an over-long `limit` or an out-of-range cursor with the static 400 codes; repair and installer backups are never overwritten within the same second; the Claude Code hook stays silent and exits 0 when a producer module fails to import, sends `finish_reason` only from a closed list, excludes normalized host names as project names; the installer refuses an install path with shell metacharacters. The Claude Code producer and the Hermes plugin send `work_type` only from the closed list (any other value is omitted). The Deploy Runbook's schema rollback is given as two explicit commands run before the code moves back.
- Dashboard: models/projects that are fully unpriced show an `unpriced` badge instead of `$0.0000`, and mixed rows show the unpriced count. Project, model, role and pricing names are HTML-escaped everywhere, and the pricing buttons read the model from `data-model`.
- The app lifespan disposes the database engine on shutdown, so the process no longer hangs at exit on an open aiosqlite connection.
- A subagent task whose parent turn is in another project now gets the correct `parent_task_ref` and root (the plugin sends a validated `parent_project_name`); existing rows are fixed by `scripts/repair_task_parents.py`.

- Privacy-first single and batch lifecycle ingest with correlation metadata.
- Concurrency-safe idempotency using project-scoped client event IDs.
- Requested/resolved/pricing model separation, model aliases, unpriced-model visibility, and recost support.
- Cache-read, cache-creation, and reasoning token dimensions.
- Deterministic `request-shape-v1` complexity analytics.
- Bounded Git repository inventory independent of event-backed activity.
- Known Repositories and Observed Activity separation in the Projects dashboard.

### Changed

- Dashboard shows input, cache read, cache write and output tokens separately (Overview, Projects, Models, Tasks); analytics `by-*`, `timeseries` and `project-inventory` rows gain `prompt_tokens`, `completion_tokens`, `cache_read_tokens`, `cache_creation_tokens` sums (additive; `total_tokens` keeps its input + output meaning).
- Production SQLite storage moved outside the repository and runs with additive migrations, backups, WAL, and busy timeout.
- Project identity now prefers canonical sanitized Git remote repository names over local directory names.
- Prompt marker attribution is disabled by default in the Hermes plugin.
- Unknown pricing is reported as unpriced rather than zero cost.

### Security

- Sensitive endpoints always require `INGEST_TOKEN` and check Origin; a Host allowlist blocks DNS rebinding.
- Prompt capture and JEV refuse to start without a token and a valid configuration.
- The pre-migration backup uses the SQLite online-backup API and is verified before any migration runs; migrations are atomic.

- Raw prompt and tool-argument capture remain disabled by default.
- Prompt payload is capped when explicitly enabled.
- Inventory exposes a hash-first workspace identity instead of absolute paths or complete Git remote URLs.
