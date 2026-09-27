# Database — Task-Level Telemetry Pilot (tasks, evaluations, evaluator runs/attempts, request composition, prompt retention)

**Lane**: database
**Tool**: Claude Code — Opus 5.5, direct (no handoff)
**Status**: not started
**Brief**: ./2026-09-27-summary.md
**Implements before**: backend.md
**Revision**: r1 (2026-09-27), plan-review round 1 fixes applied (items 3, 5, 9, 11, 18, 19, 20, 22, 23, 24)
**Revision**: r2 (2026-09-27), plan-review round 2 fixes applied (items 1, 2, 4, 5, 6, 7, plus the explicit-migration-transaction gap from r2-A UNSURE). **Plan LOCKED** — later changes go to the status.md Drift Log only.

## Goal

Add schema version 10 as an additive SQLite migration:
- new tables `tasks`, `task_evaluations`, `evaluator_runs`, `evaluator_attempts`, `provider_cooldowns` and `retention_state`;
- request-composition, complexity-provenance and task-lookup additions on `token_events`;
- a one-time, **metadata-only** backfill of `tasks` from the existing `token_events.task_id` values (with completion signals and deterministic start complexity).

The migration is preceded by an **integrity-verified** online backup. No pre-existing `token_events` column value changes.

## Existing Conventions This Lane Must Follow

- **Migration layout.** Migrations live in `migrations.py` as `async def _mN(conn: AsyncConnection)`. Each is registered in `MIGRATIONS` and recorded in `schema_migrations(version, applied_at)`. `LATEST_SCHEMA_VERSION` is currently `9`. This lane adds `_m10` and sets it to `10`.
- **`init_db()` order:**
  1. `backup_database_if_needed(DB_PATH)`
  2. `engine.begin()` → `SQLModel.metadata.create_all` → `run_migrations(conn)` → WAL check, all inside **one transaction**. SQLite DDL is transactional, so an exception anywhere rolls back `create_all` and every migration step together.

  **Explicit transaction required (r2).** Python's `sqlite3` driver (and aiosqlite on top of it) does not emit `BEGIN` before DDL. Without help, `create_all` and each `ALTER` would autocommit and the one-transaction claim would be false. So `init_db()` runs `create_all` + `run_migrations` on a **dedicated migration engine** using the SQLAlchemy pysqlite recipe:
  - a `connect` listener sets the driver's `isolation_level = None`;
  - a `begin` listener emits `BEGIN IMMEDIATE`.

  The recipe applies to this migration engine only. The request engine and its pragmas are unchanged, and the migration engine is disposed before the request engine is used. AC3f proves the rollback, with failures injected both before and during `_m10`.
- **Gotcha: model DDL is the effective DDL.** `create_all` creates any missing table from the SQLModel class *before* `_m10` runs, so the SQLModel classes in `models.py` define the new tables on both fresh and existing DBs. Consequences:
  - `_m10` must use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` with DDL that exactly matches the model.
  - For already-existing tables (`token_events`), `create_all` adds neither columns nor indexes. New columns and indexes must be added by `_m10` via `_add_columns` / `_indexes`, and also declared on `TokenEvent`.
- **Timestamps** are ISO-8601 UTC text `YYYY-MM-DDTHH:MM:SS.ffffffZ` (`models._now()`).
- **Required pragmas** stay: WAL, `busy_timeout=5000`, `foreign_keys=ON`, `synchronous=NORMAL`.
- **No CHECK constraints** on existing tables. Validation happens in the app layer, and this lane follows that.
- **Production DB** is `/home/dogukan/.local/share/token-inspector/token-inspector.db` on hermes. It is never reset or replaced. The `sqlite3` CLI is not installed on hermes; use Python `sqlite3` with a `file:…?mode=ro` URI for read-only checks.
- **Session type.** Session-based access uses `from sqlmodel.ext.asyncio.session import AsyncSession`.

## D0 — Discovery (blocking; read-only on hermes; owner: Claude Code)

This lane may not implement `_m10`'s backfill semantics until D0 evidence is recorded in `Plans/task-telemetry-jev-pilot/discovery-evidence.md`. That file holds counts, field names and hook names only, never prompt or response content. The user acknowledges it in status.md.

DB questions (read-only Python):

```python
import sqlite3
c = sqlite3.connect("file:/home/dogukan/.local/share/token-inspector/token-inspector.db?mode=ro", uri=True)
Q = {
 "sqlite_version": "SELECT sqlite_version()",
 "distinct_tasks": "SELECT COUNT(*) FROM (SELECT 1 FROM token_events WHERE task_id IS NOT NULL AND task_id <> '' GROUP BY project_name, task_id)",
 "tasks_multi_session": "SELECT COUNT(*) FROM (SELECT 1 FROM token_events WHERE task_id IS NOT NULL GROUP BY project_name, task_id HAVING COUNT(DISTINCT session_id) > 1)",
 "turns_per_task": "SELECT AVG(n), MAX(n) FROM (SELECT COUNT(DISTINCT turn_id) n FROM token_events WHERE task_id IS NOT NULL GROUP BY project_name, task_id)",
 "tasks_with_llm_complexity": "SELECT COUNT(*) FROM (SELECT 1 FROM token_events WHERE task_id IS NOT NULL AND event_type='llm_request' AND complexity IS NOT NULL GROUP BY project_name, task_id)",
 "tasks_per_day_last7": "SELECT substr(first_ts,1,10) d, COUNT(*) FROM (SELECT MIN(COALESCE(occurred_at, recorded_at)) first_ts FROM token_events WHERE task_id IS NOT NULL AND task_id <> '' GROUP BY project_name, task_id) GROUP BY d ORDER BY d DESC LIMIT 7",
 "complexity_method_tags": "SELECT json_extract(tags_json,'$.complexity_method'), COUNT(*) FROM token_events WHERE complexity IS NOT NULL GROUP BY 1",
 "session_event_tags": "SELECT json_extract(tags_json,'$.session_phase'), status, COUNT(*) FROM token_events WHERE event_type='session' GROUP BY 1,2",
}
for k, q in Q.items(): print(k, c.execute(q).fetchall())
```

Hook-level evidence also belongs in D0. Backend.md Part B owns the plugin side:
- which hook mints `task_id`;
- whether one `task_id` = one user prompt → final stop;
- which hook signals session end/finalize;
- whether a delegation exposes a parent id.

Decision rules:
- **task_id scope.** If a typical `task_id` spans several user turns, or the hook evidence shows it is session-scoped, then **stop**: log it in the Drift Log and escalate. The backfill key would have to change.
- **Complexity provenance: one candidate rule (r2 item 6).** Backfill and ingest both call the same function `start_complexity_candidate(event) -> method | None`, applied **before** ordering.
  - `method` = `token_events.complexity_method` if set, else `tags.complexity_method`, else NULL.
  - An event is a candidate iff `event_type='llm_request'`, `complexity` is non-null and `method ∈ APPROVED_METHODS = {'request-shape-v1'}`. The function then returns `'request-shape-v1'`.
  - An event with an explicit **non-approved** method (e.g. `ai-v0`) is **never** a candidate, in either path.
  - **Historical inference exception (backfill only).** An event with a NULL method (no field, no tag) is a candidate returning `'request-shape-v1-inferred'` **only** if both of these hold:
    - D0 records evidence that the plugin was the sole writer and computed request-shape-v1 (prod raw prompts = 0, so the legacy AI scorer could not run);
    - the constant `BACKFILL_INFER_RS1 = True` is set from that evidence.
  - At ingest, NULL-method events are never candidates, because the updated plugin always sends a method.
  - Among candidates, the earliest by `(COALESCE(occurred_at, recorded_at), id)` wins.
  - No value is ever labelled `request-shape-v1` without provenance.
- **Session end.** If a historical session-end marker exists (`session_event_tags`), the backfill uses it for `completion='session_end'`. Otherwise it uses only `next_task`.
- **SQLite version.** If `sqlite_version < 3.35`, only the code-only rollback is available.

## Schema Changes

```sql
-- forward (_m10), inside the single engine.begin() transaction

CREATE TABLE IF NOT EXISTS tasks (
    id                         TEXT PRIMARY KEY NOT NULL,   -- task_ref = sha256(project_name || x'1f' || task_id)[:32]
    project_name               TEXT NOT NULL,
    task_id                    TEXT NOT NULL,
    session_id                 TEXT,
    parent_task_id             TEXT,                        -- external id, same project
    root_task_id               TEXT,                        -- external id, same project
    hierarchy_status           TEXT NOT NULL DEFAULT 'unknown', -- 'root' | 'child' | 'unknown'
    source                     TEXT NOT NULL DEFAULT 'ingest',  -- 'backfill' | 'ingest'
    first_seen_at              TEXT NOT NULL,
    last_seen_at               TEXT NOT NULL,
    completion                 TEXT,                        -- 'session_end' | 'next_task' | NULL (API derives 'inferred'/'open')
    completed_at               TEXT,
    start_complexity           INTEGER,                     -- 1..5; only with non-null method
    start_complexity_method    TEXT,                        -- 'request-shape-v1' | 'request-shape-v1-inferred'
    start_complexity_event_at  TEXT,                        -- ordering key part 1
    start_complexity_event_id  TEXT,                        -- ordering key part 2 (token_events.id), tie-breaker
    prompt_text                TEXT,                        -- REDACTED; unreadable after expiry; purged to NULL
    prompt_hash                TEXT,                        -- sha256 of stored redacted text (permanent)
    prompt_length              INTEGER,                     -- chars before truncation (permanent)
    prompt_truncated           INTEGER NOT NULL DEFAULT 0,
    prompt_redaction_version   TEXT,
    prompt_captured_at         TEXT,                        -- PRODUCER capture time (validated); first write wins; never cleared
    prompt_expires_at          TEXT,                        -- prompt_captured_at + 30 days (never extended)
    prompt_purged_at           TEXT,
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_project_task   ON tasks(project_name, task_id);
CREATE INDEX        IF NOT EXISTS ix_tasks_session         ON tasks(session_id, first_seen_at);
CREATE INDEX        IF NOT EXISTS ix_tasks_project_parent  ON tasks(project_name, parent_task_id);
CREATE INDEX        IF NOT EXISTS ix_tasks_last_seen       ON tasks(last_seen_at, id);
CREATE INDEX        IF NOT EXISTS ix_tasks_prompt_expires  ON tasks(prompt_expires_at) WHERE prompt_text IS NOT NULL;

CREATE TABLE IF NOT EXISTS evaluator_runs (
    id                TEXT PRIMARY KEY NOT NULL,          -- run_id (uuid4)
    evaluator         TEXT NOT NULL,                      -- 'jev'
    status            TEXT NOT NULL,                      -- 'queued' | 'running' | 'done' | 'stopped' | 'aborted'
    requested_count   INTEGER NOT NULL,
    queued_count      INTEGER NOT NULL,
    stop_reason       TEXT,                               -- 'budget_exceeded' | 'deferred_rate_limited' | 'auth_error' | 'restart' | NULL
    retry_not_before  TEXT,                               -- set when deferred
    queued_at         TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_evaluator_runs_active ON evaluator_runs(evaluator) WHERE status IN ('queued','running');

CREATE TABLE IF NOT EXISTS task_evaluations (
    id                  TEXT PRIMARY KEY NOT NULL,
    task_ref            TEXT NOT NULL REFERENCES tasks(id),
    run_id              TEXT REFERENCES evaluator_runs(id),  -- jev only
    evaluator           TEXT NOT NULL,                      -- 'jev' | 'human'
    rubric_version      TEXT NOT NULL,                      -- 'difficulty-v0'
    status              TEXT NOT NULL,                      -- jev: 'ok' | 'error' | 'rate_limited' | 'deferred'; human: 'ok' (labelled) | 'skipped' (skipped_by_labeler) (r2 item 7)
    label               INTEGER,                            -- human: 0..4 when status='ok'; NULL when 'skipped'
    labeler             TEXT,                               -- human: ^[a-z0-9._-]{1,32}$
    note                TEXT,                               -- human: scrubbed, <=280 chars, NULLed 30 days after evaluated_at
    raw_score           REAL,
    confidence          REAL,
    probabilities_json  TEXT,                               -- JSON array of exactly 5 finite floats in [0,1]
    legend_json         TEXT,                               -- JSON array of exactly 5 strings (must equal rubric criteria)
    model               TEXT,
    provider_used       TEXT,                               -- from response routing.finalProvider
    input_hash          TEXT,
    input_tokens        INTEGER,                            -- sum of reconciled attempts
    cost_usd            REAL,                               -- sum of reconciled attempts
    http_attempts       INTEGER NOT NULL DEFAULT 0,
    latency_ms          INTEGER,
    error_type          TEXT,
    evaluated_at        TEXT NOT NULL
);
CREATE INDEX        IF NOT EXISTS ix_task_evaluations_task       ON task_evaluations(task_ref);
CREATE INDEX        IF NOT EXISTS ix_task_evaluations_eval_time  ON task_evaluations(evaluator, evaluated_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_evaluations_jev_ok     ON task_evaluations(task_ref, rubric_version, input_hash)
    WHERE evaluator = 'jev' AND status = 'ok';
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_evaluations_human      ON task_evaluations(task_ref, rubric_version, labeler)
    WHERE evaluator = 'human';

CREATE TABLE IF NOT EXISTS evaluator_attempts (
    id                  TEXT PRIMARY KEY NOT NULL,
    run_id              TEXT NOT NULL REFERENCES evaluator_runs(id),
    task_ref            TEXT NOT NULL REFERENCES tasks(id),
    provider            TEXT NOT NULL,                      -- provider requested via gateway.only
    status              TEXT NOT NULL,                      -- 'reserved' | 'succeeded' | 'failed' | 'uncertain'
    reserved_tokens     INTEGER NOT NULL,                   -- conservative upper bound
    reserved_cost_usd   REAL NOT NULL,
    actual_input_tokens INTEGER,                            -- from response usage
    actual_cost_usd     REAL,                               -- from provider_metadata.gateway.cost
    http_status         INTEGER,
    error_type          TEXT,
    retry_after_s       REAL,                               -- parsed Retry-After, if any
    started_at          TEXT NOT NULL,                      -- reservation time (committed BEFORE the HTTP call)
    completed_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_evaluator_attempts_started ON evaluator_attempts(started_at);
CREATE INDEX IF NOT EXISTS ix_evaluator_attempts_run     ON evaluator_attempts(run_id);

CREATE TABLE IF NOT EXISTS provider_cooldowns (            -- r2 item 4: persisted per-provider Retry-After deadlines
    provider     TEXT PRIMARY KEY NOT NULL,                 -- 'typesafe-ai' | 'digitalocean'
    not_before   TEXT NOT NULL,                             -- no call to this provider before this instant
    reason       TEXT NOT NULL,                             -- 'retry_after' | 'backoff'
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retention_state (               -- r2 items 1-2: durable retention bookkeeping
    key         TEXT PRIMARY KEY NOT NULL,                  -- 'pending_wal_checkpoint' | 'last_purge_at' | 'last_purge_ok' | 'last_error' | 'all_copy_purge_verified_at'
    value       TEXT,
    updated_at  TEXT NOT NULL
);

-- token_events additive columns (via _add_columns)
ALTER TABLE token_events ADD COLUMN request_system_chars        INTEGER;
ALTER TABLE token_events ADD COLUMN request_history_chars       INTEGER;
ALTER TABLE token_events ADD COLUMN request_tool_output_chars   INTEGER;
ALTER TABLE token_events ADD COLUMN request_file_content_chars  INTEGER;
ALTER TABLE token_events ADD COLUMN request_file_ref_count      INTEGER;
ALTER TABLE token_events ADD COLUMN request_tool_names_json     TEXT;
ALTER TABLE token_events ADD COLUMN complexity_method           TEXT;   -- provenance of token_events.complexity for NEW rows

CREATE INDEX IF NOT EXISTS ix_token_events_project_task ON token_events(project_name, task_id);

-- rollback (manual; see Migration Strategy → Rollback runbook first)
DROP TABLE IF EXISTS retention_state;
DROP TABLE IF EXISTS provider_cooldowns;
DROP TABLE IF EXISTS evaluator_attempts;
DROP TABLE IF EXISTS task_evaluations;
DROP TABLE IF EXISTS evaluator_runs;
DROP TABLE IF EXISTS tasks;
DROP INDEX IF EXISTS ix_token_events_project_task;
ALTER TABLE token_events DROP COLUMN request_system_chars;        -- SQLite >= 3.35
ALTER TABLE token_events DROP COLUMN request_history_chars;
ALTER TABLE token_events DROP COLUMN request_tool_output_chars;
ALTER TABLE token_events DROP COLUMN request_file_content_chars;
ALTER TABLE token_events DROP COLUMN request_file_ref_count;
ALTER TABLE token_events DROP COLUMN request_tool_names_json;
ALTER TABLE token_events DROP COLUMN complexity_method;
DELETE FROM schema_migrations WHERE version = 10;
```

Historical rows keep `token_events.complexity_method` NULL. The column is **not** backfilled, so no old column changes. Provenance for historical rows lives only in `tasks.start_complexity_method`.

### SQLModel declarations (`models.py`; must match the DDL exactly)

- `Task`, `TaskEvaluation`, `EvaluatorRun`, `EvaluatorAttempt`, `ProviderCooldown` and `RetentionState` classes, with `__table_args__` indexes. Partial indexes use `Index(..., sqlite_where=text(...))`, following the existing `TokenEvent` pattern.
- FKs are declared with `Field(foreign_key=...)`.
- `TokenEvent` gets the 7 new Optional columns plus `Index("ix_token_events_project_task", "project_name", "task_id")`.
- Columns with `NOT NULL DEFAULT` (`prompt_truncated`, `http_attempts`, `hierarchy_status`, `source`) use `sa_column_kwargs={"server_default": ...}` so that fresh and migrated schemas are identical. AC3c verifies this.

## Indexes and Constraints

- `ux_tasks_project_task` is the ingest upsert target. `tasks.id` is deterministic, so backfill and ingest agree and a pilot sample file stays valid on a restored backup.
- `ux_evaluator_runs_active` allows at most one `queued`/`running` run per evaluator. The `/evaluate` handler relies on this unique index for **atomic run reservation**: an IntegrityError becomes 409.
- `evaluator_attempts` rows are inserted and **committed before** each HTTP call. That makes them the durable budget ledger. Budget sums: `SUM(COALESCE(actual_cost_usd, reserved_cost_usd))` and `COUNT(*)` over `started_at` in the current UTC day or month.
- There are no FKs from `token_events` to `tasks`, and none on `parent_task_id`/`root_task_id`, because of out-of-order arrival.
- `ix_tasks_last_seen (last_seen_at, id)` supports the stable list order `last_seen_at DESC, id ASC`.
- `ix_tasks_session (session_id, first_seen_at)` supports `recompute_session_completion`.
- `provider_cooldowns` has one row per provider. It is upserted with `not_before = MAX(existing, new)` and committed immediately, so it survives restarts (r2 item 4).
- `retention_state` is a key/value table:
  - `pending_wal_checkpoint` persists unfinished WAL truncation across runs and restarts (r2 item 1);
  - `all_copy_purge_verified_at` gates purge interval 0 (r2 item 2).

## Pre-Migration Backup (`migrations.backup_database_if_needed`), revised

1. Copy with the SQLite online-backup API instead of `shutil.copy2`, so WAL-resident pages are included:
   ```python
   with sqlite3.connect(path) as src, sqlite3.connect(target) as dst:
       src.backup(dst)
   ```
2. **Verify before any mutation:**
   - open `target` read-only;
   - `PRAGMA integrity_check` must return `ok`;
   - `SELECT COUNT(*) FROM token_events` must equal the source count;
   - `SELECT MAX(version) FROM schema_migrations` must equal `current`.

   On any failure, raise `RuntimeError("pre-migration backup verification failed: …")`. `init_db()` aborts **before** `create_all` or migrations touch the DB (fail closed), and the failed target file is kept for inspection.
3. Keep the existing naming (`<name>.bak-v<current>-<stamp>`), the version check and the skip conditions.

## Connection Pragma Addition (`database._configure_sqlite`)

Add `PRAGMA secure_delete=ON`, so freed pages from purged prompt text are zeroed in the main DB file.

WAL frames and backups are handled by the backend retention job: `wal_checkpoint(TRUNCATE)` plus backup re-purge. See backend.md §5.

## Data Backfill (metadata only; Python inside `_m10`; idempotent)

1. **Groups.** `SELECT project_name, task_id, MIN(session_id), MIN(COALESCE(occurred_at, recorded_at)), MAX(COALESCE(occurred_at, recorded_at)) FROM token_events WHERE task_id IS NOT NULL AND task_id <> '' GROUP BY project_name, task_id`.
2. **Start complexity** uses deterministic ordering identical to ingest. Fetch `SELECT project_name, task_id, id, complexity, complexity_method, tags_json, COALESCE(occurred_at, recorded_at) AS ts FROM token_events WHERE task_id IS NOT NULL AND task_id <> '' AND event_type='llm_request' AND complexity IS NOT NULL ORDER BY project_name, task_id, ts, id`. Take the **first row per task**. Do not use SQLite's bare-column `MIN()` behaviour, because ties are non-deterministic.
   - Rows are filtered through `start_complexity_candidate()` **before** taking the first row (r2 item 6), and the returned method is stored. If there is no candidate, `start_complexity` and its method are both NULL.
   - Set `start_complexity_event_at = ts` and `start_complexity_event_id = id`.
3. **Completion.** Call the shared `recompute_session_completion(conn, project_name, session_id)` (backend.md §2, r2 item 5) once per distinct `(project_name, session_id)`. Backfill and live ingest therefore produce identical results from the same events, whatever the arrival order. The rules:
   - Order the session's tasks by `(first_seen_at, id)`.
   - Every task that has a later-starting task in the same project+session gets `completion='next_task'`, with `completed_at` = that next task's `first_seen_at`.
   - The last task gets `completion='session_end'` if a session-end marker exists (per D0: `event_type='session'`, `tags.session_phase='end'`, same project+session, `occurred_at ≥ task.first_seen_at`). Its `completed_at` = the earliest such marker.
   - Otherwise the last task stays NULL, and the API derives `inferred` or `open`.
4. **Insert** with `INSERT OR IGNORE INTO tasks`, setting:
   - `source='backfill'`
   - `hierarchy_status='unknown'` (historical events carry no delegation data)
   - `parent_task_id` and `root_task_id` NULL
   - all nullable `prompt_*` columns NULL and `prompt_truncated=0`
   - `created_at = updated_at = now`
5. **Pilot impact.** Backfilled tasks are **metadata-only**. They have no prompt, so they are never pilot-eligible. The pilot is prospective (brief AC4).

## Migration Strategy

- **Forward:** a single transaction inside `engine.begin()`, run at service startup, so no concurrent writers.
  - **Deploy the plugin spool (backend.md Part B, 0b) before this migration**, so batches sent during the restart are spooled rather than dropped.
  - Expected backup: `token-inspector.db.bak-v9-<stamp>`, verified as above before migrating.
- **Interrupted migration:** because `create_all` and all migrations share one transaction, an exception or crash leaves the DB at schema v9 with no partial tables. The next start re-takes a backup and retries. tests-other verifies this.
- **Rollback runbook (privacy-first).** v9 code has no retention worker, so any retained prompts would otherwise live forever. The runbook:
  1. Set `STORE_TASK_PROMPTS=0` and `JEV_ENABLED=0`, set plugin `capture_task_prompt=false`, and restart both.
  2. From the v10 checkout, run `python scripts/purge_task_prompts.py --db <prod path> --all --include-backups --verify`. This is standalone stdlib `sqlite3` with no app imports (backend.md §5). It nulls every `tasks.prompt_text` and every human `note`, sets `prompt_purged_at`, runs `secure_delete` + `wal_checkpoint(TRUNCATE)`, and re-purges + VACUUMs the DB-directory backups.
  3. Then do one of:
     - *Code-only rollback (preferred):* revert application code and leave schema v10. The v9 code ignores the extra tables and nullable columns, and `run_migrations` skips the recorded version 10. tests-other verifies that v9 starts and ingests against a v10 DB.
     - *Schema rollback:* export `task_evaluations` (labels/JEV) with a read-only dump to a file outside Git, then run the rollback SQL. Requires SQLite ≥ 3.35.
  4. Restoring `.bak-v9-*` is allowed only if no events were ingested after the migration.
- **Lock implications:**
  - `ALTER TABLE ADD COLUMN` is O(1).
  - The index build scans about 9k rows.
  - The backfill touches a few thousand task rows.
  - Well under a second in total, before the service serves.

## Acceptance Criteria

- **AC3a:** Fresh DB → `schema_migrations` max = 10. All six new tables exist with exactly the listed columns and indexes, including the partial `WHERE` clauses.
- **AC3b:** Migrating a **pinned genuine v9 schema** (`tests/fixtures/schema_v9.sql`) with representative data leaves **every pre-existing `token_events` column value unchanged row-by-row**, keyed by `id`, compared across all v9 columns. All 7 new columns are NULL on old rows. On hermes, the same row-by-row comparison runs on a verified online-backup clone of prod (9,146 rows).
- **AC3c:** Fresh-DB and migrated-v9 schemas are identical (`sqlite_master.sql`, `table_info`, `index_list`) for all new tables and `token_events`.
- **AC3d:** The pre-migration backup uses the online-backup API, includes WAL-resident rows, and is integrity- and count-verified **before** migration. A failed verification aborts startup with the DB untouched.
- **AC3e:**
  - The rollback SQL restores the v9 `token_events` schema.
  - The **v9 application** (a git worktree at `0aab993`) starts, ingests an event and serves `/api/analytics/summary` against a migrated v10 DB.
  - `scripts/purge_task_prompts.py --all` leaves no prompt text or notes.
- **AC3f:** A failure injected inside `_m10` leaves `schema_migrations` max = 9 and no new tables. A subsequent start succeeds.
- **AC4a:** The backfill creates one task per distinct non-empty `(project_name, task_id)`, with:
  - first/last seen = MIN/MAX;
  - `start_complexity` from the earliest `(ts, id)` llm_request, with the D0-approved method, or NULL;
  - `hierarchy_status='unknown'` and `source='backfill'`;
  - `completion='next_task'` for all but the last task per session;
  - `prompt_text`, `prompt_hash`, `prompt_length`, `prompt_redaction_version`, `prompt_captured_at`, `prompt_expires_at`, `prompt_purged_at` all NULL, and `prompt_truncated = 0`.
- **AC4b:** Re-running `_m10` / `init_db` inserts no duplicates and changes no task rows.
- **AC4d:** With two llm_request events at the **same timestamp**, the backfill and live ingest choose the same start complexity (lowest `id`).
- **AC5a:** `PRAGMA secure_delete` = 1 on service connections.
- **AC4e (r2 item 6):**
  - Events with an explicit non-approved method are never chosen as start complexity, in either path.
  - The NULL-method `request-shape-v1-inferred` exception applies only in the backfill, and only with `BACKFILL_INFER_RS1`.
  - For explicit-method events, backfill and ingest choose the same value.
- **AC4f (r2 item 5):** Backfill completion equals live-ingest completion for the same event set, independent of arrival order.
- **AC3g (r2):** `provider_cooldowns` and `retention_state` exist with the listed columns in both the fresh and the migrated schema (covered by the AC3a/AC3c tests).

## Out of Scope

- Changing historical `token_events` values, including backfilling `complexity_method` on old rows.
- Faz 1 source columns and cross-source dedup.
- Any second database engine.
- Backups outside the DB directory. Manual copies are the operator's responsibility and are documented in ADR-002.
- FKs between `token_events` and `tasks`.
