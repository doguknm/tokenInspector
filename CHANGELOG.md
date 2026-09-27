# Changelog

All notable user-visible and operational changes are documented here.

## Unreleased

### Added

- Complexity view: tier × model comparison (calls, tasks, per-call and per-task token split, latency, TTFT, error rate, priced cost per task, completion counts, low-sample marks) with project/model/method/day filters, backed by the new `GET /api/analytics/complexity-matrix`; the demo seed now carries per-call tiers and two priced models.
- Task telemetry (schema v10): one task per Hermes turn, derived at ingest and backfilled metadata-only, with deterministic start complexity, arrival-order-independent completion and hierarchy from subagent starts.
- Tasks view and Tasks API (list, detail, blind labels), `/api/meta` feature gates and config preflight.
- Gated, scrubbed task prompt storage (`STORE_TASK_PROMPTS`) with 30-day retention from producer capture time across DB, WAL and backups (ADR-002).
- JEV difficulty worker via Vercel AI Gateway with durable per-call budget reservations, persisted provider cooldowns and exact Retry-After handling.
- JEV pilot CLI (`jev_pilot.py`) with a paired-cohort report and pre-registered thresholds.
- Request-composition counts on LLM events; `scripts/purge_task_prompts.py`, `scripts/seed_tasks_demo.py`, `scripts/rollback_v10.sql`.

### Fixed

- Dashboard: models/projects that are fully unpriced show an `unpriced` badge instead of `$0.0000`, and mixed rows show the unpriced count. Project, model, role and pricing names are HTML-escaped everywhere, and the pricing buttons read the model from `data-model`.
- The app lifespan disposes the database engine on shutdown, so the process no longer hangs at exit on an open aiosqlite connection.

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
