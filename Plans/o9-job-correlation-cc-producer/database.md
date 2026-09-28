# Database — O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export

**Lane**: database
**Tool**: Claude Code — Opus 5.5, direct (no handoff)
**Status**: not started
**Brief**: ./2026-09-28-summary.md
**Implements before**: backend.md (Phase 1 needs v11 before backend Phase 1 steps J3–J6; Phase 3 needs v12 before X1)
**Repo**: backend only — `C:\Users\Bentego_Admin\Projects\tokenInspector`, branch `feat/task-telemetry-jev-pilot`. The plugin repo has no database.

## Goal

Two additive schema migrations, one per phase that needs one:

- **v11 (Phase 1):** each task remembers the one job it belongs to (`tasks.job_ref`, first write wins) and counts conflicting later job refs (`tasks.job_ref_conflicts`). This is what makes AC1.5 "a task belongs to at most one job; a conflicting later `job_ref` is counted as an anomaly and does not overwrite" enforceable in SQL. v11 also adds the cross-project unique index for Claude Code ids (`ux_token_events_cc_client_event`, used from Phase 2 C10; plan review r1 A-F7/B-F5).
- **v12 (Phase 3):** every event gets a monotonic ingest sequence (`token_events.ingest_seq`), assigned in commit order from a **persistent high-water** that never goes back, even after deletions (plan review r1 A-F2), plus a one-row `export_state` table that also carries the export `revision` and `epoch` used to invalidate cursors after mutations of snapshotted data (A-F1/B-F7). The export uses the sequence as a snapshot high-water mark and as the event keyset, so concatenated pages have no duplicates or gaps under concurrent ingest (AC3.4).

Phase 2 has no schema change of its own (its index ships in v11). Job context itself stays in `token_events.tags_json` (brief: "Keep job context in `tags`"); no new `token_events` column carries job data.

## Conventions this lane must follow (from AGENTS.md Gotchas, repo code)

- `SQLModel.metadata.create_all` runs **before** migrations, so the model classes define new columns. New fields are **appended at the end** of `models.Task` / `models.TokenEvent`, and the migration adds them with `_add_columns` **in declaration order**, with the same DDL types `create_all` emits, so fresh and migrated schemas agree (same rule as `_M10_TOKEN_EVENT_COLUMNS`).
- The migration engine emits `BEGIN IMMEDIATE`; each migration runs in that one transaction. Do not add a separate `COMMIT`.
- `LATEST_SCHEMA_VERSION` in `migrations.py` is bumped per migration (10 → 11 in Phase 1, 11 → 12 in Phase 3). `backup_database_if_needed` then takes and verifies the online pre-migration backup automatically (`<db>.bak-v<current>-<stamp>`); do not change that function.
- `/api/meta` returns `LATEST_SCHEMA_VERSION`. The plugin probe requires `schema_version >= 10`, so 11 and 12 keep capture probing unchanged (all capture flags stay off anyway).
- SQLite files and backups are never committed. Tests build DBs in `tmp_path`.

---

## Phase 1 — v11: task → job

### Schema Changes

```sql
-- forward (_m11, inside the migration transaction)
ALTER TABLE tasks ADD COLUMN job_ref VARCHAR;                              -- NULL = task has no job
ALTER TABLE tasks ADD COLUMN job_ref_conflicts INTEGER NOT NULL DEFAULT 0; -- later events with a different valid job_ref
CREATE INDEX IF NOT EXISTS ix_tasks_job_ref ON tasks (job_ref) WHERE job_ref IS NOT NULL;
-- Claude Code ids are unique across projects (a resumed copy resolved to another project is a duplicate)
CREATE UNIQUE INDEX IF NOT EXISTS ux_token_events_cc_client_event
    ON token_events (client_event_id) WHERE substr(client_event_id, 1, 3) = 'cc-';

-- rollback (scripts/rollback_v11.sql, run only through scripts/rollback_schema.py --to 10; service stopped)
BEGIN IMMEDIATE;
DROP INDEX IF EXISTS ux_token_events_cc_client_event;
DROP INDEX IF EXISTS ix_tasks_job_ref;
ALTER TABLE tasks DROP COLUMN job_ref_conflicts;
ALTER TABLE tasks DROP COLUMN job_ref;
DELETE FROM schema_migrations WHERE version = 11;
COMMIT;
```

The `cc-` prefix is reserved for the Claude Code producer (ADR-004). No stored row has it before Phase 2 ships (single deploy), so the unique index builds on prod data without conflict; `_m11` still checks `SELECT COUNT(*) … GROUP BY client_event_id HAVING COUNT(*) > 1` over `cc-` rows first and aborts the migration with a fixed message if any exist. `substr` is deterministic, so it is valid in a partial-index predicate (`LIKE` is not used: it depends on `PRAGMA case_sensitive_like`).

Model (`models.py`, appended after `updated_at` in `Task`):

```python
job_ref: Optional[str] = None
job_ref_conflicts: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
```

and in `Task.__table_args__`: `Index("ix_tasks_job_ref", "job_ref", sqlite_where=text("job_ref IS NOT NULL"))`; in `TokenEvent.__table_args__`: `Index("ux_token_events_cc_client_event", "client_event_id", unique=True, sqlite_where=text("substr(client_event_id, 1, 3) = 'cc-'"))`.

`_m11` does: `_add_columns(conn, "tasks", {"job_ref": "VARCHAR", "job_ref_conflicts": "INTEGER NOT NULL DEFAULT 0"})` (verify the exact DDL string `create_all` emits for these two fields with a fresh-vs-migrated `PRAGMA table_info` comparison test and match it), then the `CREATE INDEX IF NOT EXISTS`, then the `cc-` duplicate check and the `CREATE UNIQUE INDEX IF NOT EXISTS`. Register `(11, _m11)` in `MIGRATIONS`.

### Indexes and Constraints

- `ix_tasks_job_ref` (partial, `job_ref IS NOT NULL`): the jobs API groups and filters by job; most tasks have no job.
- No foreign key: `job_ref` is a launcher id, not a row in any table (there is no jobs table; jobs are an aggregate).
- No CHECK on the job_ref format: the backend normalizes before writing (backend.md J1, `JOB_REF_RE = ^(devir-)?[0-9]{8}-[0-9]{6}-[0-9]{1,10}$`); the column never receives an unvalidated value.
- `ux_token_events_cc_client_event` (unique, partial on the `cc-` prefix): makes a CC provider call count once across projects (AC2.4). The existing per-project index stays for every other producer.
- Write rule (implemented in `task_store._upsert_task`, backend.md J4, listed here so the schema intent is explicit):
  - `job_ref = COALESCE(tasks.job_ref, excluded.job_ref)` — first valid job wins, never overwritten.
  - `job_ref_conflicts = tasks.job_ref_conflicts + CASE WHEN tasks.job_ref IS NOT NULL AND excluded.job_ref IS NOT NULL AND excluded.job_ref <> tasks.job_ref THEN 1 ELSE 0 END`.
  - Duplicate deliveries skip task derivation (existing behaviour), so a replay never increments the counter.

### Data Backfill

None. No stored event carries a `job_ref` tag before this release (the plugin sends it only after Phase 1, and the backend is deployed first — AC1.4). `job_ref` starts NULL and `job_ref_conflicts` 0 for every existing task.

### Migration Strategy

- Forward: one `BEGIN IMMEDIATE` transaction, two `ADD COLUMN` (metadata-only in SQLite, O(1)) and one index build over `tasks` (small: a few thousand rows in prod). Pre-migration backup is automatic and verified.
- Rollback safety: **code-only rollback is safe** — the v10 app never names the new columns; inserts leave `job_ref` NULL and `job_ref_conflicts` at its default. (The CC hooks are uninstalled before any code-only rollback below Phase 2 code — status.md Rollback — so v10 code never meets a cross-project `cc-` duplicate.) Schema rollback only through `scripts/rollback_schema.py` (see "Schema rollback runner" below; service stopped) drops the job assignment; nothing else is lost. Add a code-only check script in the style of `scripts/check_v9_app_on_v10.py` only if the test below cannot cover it (it should: see AC-DB1.4).
- Lock implications: the migration holds the write lock for well under a second; the service runs it at startup before serving, so no concurrent writer exists.

### Acceptance Criteria (Phase 1)

- AC-DB1.1 (supports AC1.5): v11 applies cleanly on a fresh database; `PRAGMA table_info(tasks)` on a fresh DB equals the migrated one (same columns, order, types, defaults).
- AC-DB1.2 (supports AC1.5): v11 applies cleanly on a v10 DB built by the existing migration fixtures (and on a copy of the demo seed DB); every existing task has `job_ref IS NULL`, `job_ref_conflicts = 0`; `schema_migrations` holds 11; the automatic `.bak-v10-*` backup exists and passes `verify_backup`.
- AC-DB1.3: `rollback_schema.py --to 10` on a v11 DB restores the v10 column set and `schema_migrations` max 10; on a v12 DB it refuses unless run as `--to 11` first (version guard); the v10 app code path (`task_store._upsert_task` without job columns) still inserts on a v11 DB.
- AC-DB1.4: re-running migrations on a v11 DB is a no-op (idempotent `_add_columns`, `IF NOT EXISTS`).
- AC-DB1.5 (supports AC2.4): two `cc-` rows with the same `client_event_id` in different projects violate `ux_token_events_cc_client_event`; the same id without the prefix in two projects is still allowed; `_m11` aborts with a fixed message (no ids echoed) on a DB that already holds cross-project `cc-` duplicates.

---

## Phase 2 — no schema change

Phase 2 (Claude Code producer) writes through the existing ingest path only. `client_event_id` idempotency uses the existing partial unique index `idx_token_events_project_client_event (project_name, client_event_id) WHERE client_event_id IS NOT NULL` plus, for `cc-` ids, the cross-project `ux_token_events_cc_client_event` shipped in v11 (backend.md C10 makes the insert treat a conflict on either index as a duplicate).

---

## Phase 3 — v12: ingest sequence for snapshot-consistent export

### Why a new column (and not `recorded_at` or `rowid`)

- `recorded_at` is computed in `_prepare_event` **before** the insert takes the write lock, so two concurrent requests can commit in the opposite order of their `recorded_at`. A keyset on it can skip a row that commits late with an earlier timestamp (gap).
- `rowid` of `token_events` (TEXT primary key, so an implicit rowid) can be renumbered by `VACUUM`, and the default rowid algorithm may reuse the largest value after a delete. Not a stable cursor.
- `ingest_seq` is assigned **inside the write transaction** from a persistent high-water `export_state.last_seq` (next = `last_seq + 1`). SQLite has one writer at a time and the value is read and advanced under the write lock, so sequence order equals commit order and no uncommitted row can ever end up below a reader's observed high-water. Unlike `MAX(ingest_seq) + 1`, the high-water never goes back when the highest rows are deleted, so a number at or below a saved `as_of` is never handed out again (plan review r1 A-F2). No app path deletes `token_events` today (retention only NULLs prompt columns); the allocator makes the guarantee independent of that.

### Schema Changes

```sql
-- forward (_m12, inside the migration transaction)
ALTER TABLE token_events ADD COLUMN ingest_seq INTEGER;
CREATE TABLE IF NOT EXISTS export_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq INTEGER NOT NULL,        -- persistent high-water of ingest_seq; never decreases
    revision INTEGER NOT NULL DEFAULT 0, -- bumped by any mutation of already-snapshotted data (backend.md X1)
    epoch TEXT NOT NULL               -- random 32-hex id written once when the row is created
);
-- backfill existing rows 1..N in (recorded_at, rowid) order: Python loop, see below
INSERT OR IGNORE INTO export_state (id, last_seq, revision, epoch)
    VALUES (1, (SELECT COALESCE(MAX(ingest_seq), 0) FROM token_events), 0, :epoch);
CREATE UNIQUE INDEX IF NOT EXISTS ux_token_events_ingest_seq
    ON token_events (ingest_seq) WHERE ingest_seq IS NOT NULL;

-- rollback (scripts/rollback_v12.sql, run only through scripts/rollback_schema.py --to 11; service stopped)
BEGIN IMMEDIATE;
DROP INDEX IF EXISTS ux_token_events_ingest_seq;
DROP TABLE IF EXISTS export_state;
ALTER TABLE token_events DROP COLUMN ingest_seq;
DELETE FROM schema_migrations WHERE version = 12;
COMMIT;
```

**Backfill without window functions (A-F21).** `_m12` and the startup self-heal share one function `assign_missing_ingest_seq(conn)` in `migrations.py`: `SELECT id FROM token_events WHERE ingest_seq IS NULL ORDER BY recorded_at, rowid`, then `executemany("UPDATE token_events SET ingest_seq = ? WHERE id = ?")` with `last_seq + 1, last_seq + 2, …` (`last_seq` = `export_state.last_seq`, or `COALESCE(MAX(ingest_seq), 0)` while the row does not exist yet), then `UPDATE export_state SET last_seq = <new max>`. It needs nothing beyond the app's minimum SQLite 3.24 (no `UPDATE … FROM`, no window functions). Only NULL rows are numbered; non-NULL values are never touched. Minimum SQLite versions are separate: forward migration and self-heal = 3.24 (app minimum, `database.init_db`); `DROP COLUMN` rollback = 3.35, checked by the rollback runner. Record the SQLite version of Windows Python and of the hermes venv in the status.md Verification Log before writing `_m12`.

`export_state` is a migration-owned table (no SQLModel class): `create_all` does not know it, and `_m12` creates it on fresh and migrated DBs alike (migrations run on fresh DBs too).

Model (`models.py`, appended **after** `request_tool_names_json` in `TokenEvent`, so declaration order matches the migration):

```python
ingest_seq: Optional[int] = None
```

and in `TokenEvent.__table_args__`: `Index("ux_token_events_ingest_seq", "ingest_seq", unique=True, sqlite_where=text("ingest_seq IS NOT NULL"))`.

### Assignment on insert (backend.md X1 implements it; the rule belongs to the schema)

- `routes/events._insert_event` sets `ingest_seq` in the same `INSERT` statement as a scalar subquery: `(SELECT last_seq + 1 FROM export_state WHERE id = 1)`. It is part of the write, so it runs under the write lock. When the row was really inserted (not a duplicate), the same transaction then runs `UPDATE export_state SET last_seq = last_seq + 1 WHERE id = 1`. The partial unique index makes a violation of the rule fail loudly instead of silently duplicating.
- A duplicate (`ON CONFLICT DO NOTHING`) consumes no sequence number (the `UPDATE` is skipped).
- JEV's own usage event (`jev_scorer`, same `_prepare_event`/`_insert_event` path) gets a sequence number like any event; the export filters it out by rule, not by sequence.

### Revision and epoch (cursor invalidation; the rule belongs to the schema)

- `export_state.revision` is incremented, in the same transaction, by every write that changes already-stored data the export reads for membership or sums: recost `--apply` with `changed > 0` (`routes/settings.py`), repair `--apply` with changes (`scripts/repair_task_parents.py`), and any future maintenance that deletes or rewrites `token_events` rows. Normal ingest does not bump it (new rows get a sequence above any saved `as_of`).
- `export_state.epoch` is written once when the row is created. A schema rollback drops the table; a re-upgrade creates a new epoch, so cursors from before the rollback (whose sequence numbers may be reassigned) are refused.
- Export cursors carry `as_of`, `revision` and `epoch`; a mismatch on a later page returns the static `snapshot_expired` error (backend.md X2/X5).

### Self-heal of NULL sequences (code-only rollback, then re-upgrade)

If v12 code is rolled back without the schema, the v11 code inserts rows with `ingest_seq` NULL (and does not touch `export_state`). After a re-upgrade, `_m12` is already recorded and would not run again, so those rows would never be exported. Therefore `assign_missing_ingest_seq(conn)` runs **at every startup** as an explicit step of `database.init_db`: inside the same `async with migration_engine.begin() as conn:` block (and the `:memory:` branch's `engine.begin()` block), right after `await run_migrations(conn)` and before the WAL check — one transaction with the migrations, so an interrupted heal leaves no partial numbering and the next startup completes it. With no NULL rows it is a single indexed-miss scan (cheap at prod size). Healed rows land above the high-water, so an open cursor never sees them (its `as_of` is lower) and a consumer sees them in the next export: the documented "late data" behaviour (consumers re-fetch periods, AC3.7).

### Indexes and Constraints

- `ux_token_events_ingest_seq` (unique, partial): keyset `ingest_seq > :after AND ingest_seq <= :as_of ORDER BY ingest_seq`. `as_of` is read from `export_state.last_seq` (one primary-key row).
- No other new index. Range filters on `COALESCE(occurred_at, recorded_at)` scan within the sequence range; acceptable at prod size (~10^4 events) and bounded by the export's 92-day range limit (backend.md X2). Revisit only if the Verification Log shows an export page above 1 s.

### Data Backfill

Existing rows get 1..N in `(recorded_at, rowid)` order inside `_m12` (Python loop above), and `export_state.last_seq = N`. It is a one-off ordering for history only; the snapshot guarantee applies to rows ingested after v12.

### Schema rollback runner (A-F20)

`scripts/rollback_schema.py --db PATH --to {11,10}` (stdlib) is the only supported way to run `rollback_v12.sql` / `rollback_v11.sql`:
- reads `MAX(version)` from `schema_migrations` and refuses (exit 1, fixed message) unless it is exactly the source version of the next step; `--to 10` on a v12 DB runs v12→11 then 11→10, in that order, each in its own transaction; it never runs `rollback_v11.sql` on a DB that still records 12;
- refuses when `sqlite3.sqlite_version_info < (3, 35)` (no `DROP COLUMN`); the fallback is restoring the pre-deploy backup;
- prints the matching application artifact for the resulting version (the backend commit whose `LATEST_SCHEMA_VERSION` equals it); starting newer code on a rolled-back DB simply re-runs the dropped migrations (re-upgrade), which the tests cover.
- Run with the service stopped (Deploy Runbook Rollback).

### Migration Strategy

- Forward: one transaction: `ADD COLUMN` (O(1)), the `export_state` table, one numbering pass (prod ~10^4 rows: well under a second), unique index build. Automatic verified backup first.
- Rollback safety: code-only rollback is safe (column nullable, index partial, the extra table is ignored by old code). Re-upgrade after a code-only rollback is covered by the startup self-heal. Schema rollback only via `scripts/rollback_schema.py`.
- Lock implications: runs at startup before serving. At runtime the high-water read and the one-row `UPDATE` add two primary-key operations per inserted event under the write lock that the insert already holds; batch ingest (≤ 500 events per transaction) is unaffected in shape.

### Acceptance Criteria (Phase 3)

- AC-DB3.1 (supports AC3.4): v12 applies cleanly on a fresh DB; fresh and migrated `PRAGMA table_info(token_events)` agree.
- AC-DB3.2: v12 applies cleanly on a v11 DB with events; every existing row gets a distinct `ingest_seq` 1..N in `(recorded_at, rowid)` order; the pre-migration backup verifies.
- AC-DB3.3 (supports AC3.4): new inserts get `last_seq + 1`; under concurrent batch ingest the sequence is gap-free, unique and in commit order (tests-other X-tests); after the highest rows are deleted, the next insert still gets a number above the old maximum (no reuse).
- AC-DB3.4: after rows are inserted with `ingest_seq` NULL (simulated old code), a restart assigns them `last_seq + 1 …` in `(recorded_at, rowid)` order and advances `last_seq`; existing non-NULL values are unchanged; a second restart changes nothing; a heal interrupted by an exception leaves no row numbered and the next startup completes it.
- AC-DB3.5: `rollback_schema.py --to 11` restores the v11 column set and drops `export_state`; v11 code inserts on a v12 DB; `--to 10` from v12 runs both steps in order; the runner refuses a wrong source version.
- AC-DB3.6 (B-F9): end to end: v10 DB → v12 code (migrates to 12) → v10/v11-shaped code writes rows (NULL `ingest_seq`, incl. a duplicate `client_event_id`) → v12 code restarts → every row has a unique sequence, pre-existing sequences are unchanged, the duplicate was not stored twice, and a cursor saved before the old-code writes still returns the same pages (healed rows are above its `as_of`); after a schema rollback and re-upgrade, that cursor is refused (`epoch` changed).
- AC-DB3.7: `export_state.revision` is incremented by recost apply with changes and by repair apply with changes, and not by ingest or by a dry-run.

## Out of Scope

- A jobs table, a job-duration column or any job-level launcher event (brief: out of scope).
- New `token_events` columns for `job_ref`, `runtime`, `work_type`, `job_attempt` or `producer` (they stay in `tags_json`).
- Storing `parent_session_id`, `parent_turn_id` or `parent_project_name` on `token_events` (the repair script re-derives parents by hash matching instead — backend.md J7).
- Any change to the O10 prompt columns, retention or JEV tables.
