# Database — O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export

**Lane**: database
**Tool**: Claude Code — Opus 5.5, direct (no handoff)
**Status**: not started
**Brief**: ./2026-09-28-summary.md
**Implements before**: backend.md (Phase 1 needs v11 before backend Phase 1 steps J3–J6; Phase 3 needs v12 before X1)
**Repo**: backend only — `C:\Users\Bentego_Admin\Projects\tokenInspector`, branch `feat/task-telemetry-jev-pilot`. The plugin repo has no database.

## Goal

Two additive schema migrations, one per phase that needs one:

- **v11 (Phase 1):** each task remembers the one job it belongs to (`tasks.job_ref`, first write wins) and counts conflicting later job refs (`tasks.job_ref_conflicts`). This is what makes AC1.5 "a task belongs to at most one job; a conflicting later `job_ref` is counted as an anomaly and does not overwrite" enforceable in SQL.
- **v12 (Phase 3):** every event gets a monotonic ingest sequence (`token_events.ingest_seq`), assigned in commit order. The export uses it as a snapshot high-water mark and as the event keyset, so concatenated pages have no duplicates or gaps under concurrent ingest (AC3.4).

Phase 2 has no schema change. Job context itself stays in `token_events.tags_json` (brief: "Keep job context in `tags`"); no new `token_events` column carries job data.

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

-- rollback (scripts/rollback_v11.sql; SQLite >= 3.35 for DROP COLUMN; run with the service stopped)
BEGIN IMMEDIATE;
DROP INDEX IF EXISTS ix_tasks_job_ref;
ALTER TABLE tasks DROP COLUMN job_ref_conflicts;
ALTER TABLE tasks DROP COLUMN job_ref;
DELETE FROM schema_migrations WHERE version = 11;
COMMIT;
```

Model (`models.py`, appended after `updated_at` in `Task`):

```python
job_ref: Optional[str] = None
job_ref_conflicts: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
```

and in `Task.__table_args__`: `Index("ix_tasks_job_ref", "job_ref", sqlite_where=text("job_ref IS NOT NULL"))`.

`_m11` does: `_add_columns(conn, "tasks", {"job_ref": "VARCHAR", "job_ref_conflicts": "INTEGER NOT NULL DEFAULT 0"})` (verify the exact DDL string `create_all` emits for these two fields with a fresh-vs-migrated `PRAGMA table_info` comparison test and match it), then the `CREATE INDEX IF NOT EXISTS`. Register `(11, _m11)` in `MIGRATIONS`.

### Indexes and Constraints

- `ix_tasks_job_ref` (partial, `job_ref IS NOT NULL`): the jobs API groups and filters by job; most tasks have no job.
- No foreign key: `job_ref` is a launcher id, not a row in any table (there is no jobs table; jobs are an aggregate).
- No CHECK on the job_ref format: the backend normalizes before writing (backend.md J1, regex `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`); the column never receives an unvalidated value.
- Write rule (implemented in `task_store._upsert_task`, backend.md J4, listed here so the schema intent is explicit):
  - `job_ref = COALESCE(tasks.job_ref, excluded.job_ref)` — first valid job wins, never overwritten.
  - `job_ref_conflicts = tasks.job_ref_conflicts + CASE WHEN tasks.job_ref IS NOT NULL AND excluded.job_ref IS NOT NULL AND excluded.job_ref <> tasks.job_ref THEN 1 ELSE 0 END`.
  - Duplicate deliveries skip task derivation (existing behaviour), so a replay never increments the counter.

### Data Backfill

None. No stored event carries a `job_ref` tag before this release (the plugin sends it only after Phase 1, and the backend is deployed first — AC1.4). `job_ref` starts NULL and `job_ref_conflicts` 0 for every existing task.

### Migration Strategy

- Forward: one `BEGIN IMMEDIATE` transaction, two `ADD COLUMN` (metadata-only in SQLite, O(1)) and one index build over `tasks` (small: a few thousand rows in prod). Pre-migration backup is automatic and verified.
- Rollback safety: **code-only rollback is safe** — the v10 app never names the new columns; inserts leave `job_ref` NULL and `job_ref_conflicts` at its default. Schema rollback via `scripts/rollback_v11.sql` (service stopped) drops the job assignment; nothing else is lost. Add a code-only check script in the style of `scripts/check_v9_app_on_v10.py` only if the test below cannot cover it (it should: see AC-DB1.4).
- Lock implications: the migration holds the write lock for well under a second; the service runs it at startup before serving, so no concurrent writer exists.

### Acceptance Criteria (Phase 1)

- AC-DB1.1 (supports AC1.5): v11 applies cleanly on a fresh database; `PRAGMA table_info(tasks)` on a fresh DB equals the migrated one (same columns, order, types, defaults).
- AC-DB1.2 (supports AC1.5): v11 applies cleanly on a v10 DB built by the existing migration fixtures (and on a copy of the demo seed DB); every existing task has `job_ref IS NULL`, `job_ref_conflicts = 0`; `schema_migrations` holds 11; the automatic `.bak-v10-*` backup exists and passes `verify_backup`.
- AC-DB1.3: `scripts/rollback_v11.sql` restores the v10 column set and `schema_migrations` max 10; the v10 app code path (`task_store._upsert_task` without job columns) still inserts on a v11 DB.
- AC-DB1.4: re-running migrations on a v11 DB is a no-op (idempotent `_add_columns`, `IF NOT EXISTS`).

---

## Phase 2 — no schema change

Phase 2 (Claude Code producer) writes through the existing ingest path only. `client_event_id` idempotency uses the existing partial unique index `idx_token_events_project_client_event (project_name, client_event_id) WHERE client_event_id IS NOT NULL`; no change.

---

## Phase 3 — v12: ingest sequence for snapshot-consistent export

### Why a new column (and not `recorded_at` or `rowid`)

- `recorded_at` is computed in `_prepare_event` **before** the insert takes the write lock, so two concurrent requests can commit in the opposite order of their `recorded_at`. A keyset on it can skip a row that commits late with an earlier timestamp (gap).
- `rowid` of `token_events` (TEXT primary key, so an implicit rowid) can be renumbered by `VACUUM`, and the default rowid algorithm may reuse the largest value after a delete. Not a stable cursor.
- `ingest_seq` is assigned **inside the write transaction** as `MAX(ingest_seq) + 1`. SQLite has one writer at a time, and a writer's `MAX` sees every committed row, so sequence order equals commit order and no uncommitted row can ever end up below a reader's observed maximum.

### Schema Changes

```sql
-- forward (_m12, inside the migration transaction)
ALTER TABLE token_events ADD COLUMN ingest_seq INTEGER;
-- backfill existing rows in (recorded_at, rowid) order, 1..N
UPDATE token_events SET ingest_seq = o.n
FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY recorded_at, rowid) AS n FROM token_events) AS o
WHERE token_events.id = o.id AND token_events.ingest_seq IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_token_events_ingest_seq
    ON token_events (ingest_seq) WHERE ingest_seq IS NOT NULL;

-- rollback (scripts/rollback_v12.sql; service stopped)
BEGIN IMMEDIATE;
DROP INDEX IF EXISTS ux_token_events_ingest_seq;
ALTER TABLE token_events DROP COLUMN ingest_seq;
DELETE FROM schema_migrations WHERE version = 12;
COMMIT;
```

`UPDATE … FROM` and window functions need SQLite ≥ 3.33; record the SQLite version of Windows Python and of the hermes venv in the status.md Verification Log before writing `_m12`. If either is older, use the equivalent temp-table form (`CREATE TEMP TABLE _seq AS SELECT id, ROW_NUMBER() … ; UPDATE token_events SET ingest_seq = (SELECT n FROM _seq WHERE _seq.id = token_events.id) WHERE ingest_seq IS NULL; DROP TABLE _seq`).

Model (`models.py`, appended **after** `request_tool_names_json` in `TokenEvent`, so declaration order matches the migration):

```python
ingest_seq: Optional[int] = None
```

and in `TokenEvent.__table_args__`: `Index("ux_token_events_ingest_seq", "ingest_seq", unique=True, sqlite_where=text("ingest_seq IS NOT NULL"))`.

### Assignment on insert (backend.md X1 implements it; the rule belongs to the schema)

- `routes/events._insert_event` sets `ingest_seq` in the same `INSERT` statement as a scalar subquery: `(SELECT COALESCE(MAX(ingest_seq), 0) + 1 FROM token_events)`. It is part of the write, so it runs under the write lock. The partial unique index makes a violation of the rule fail loudly instead of silently duplicating.
- A duplicate (`ON CONFLICT DO NOTHING`) consumes no sequence number.
- JEV's own usage event (`jev_scorer`, same `_prepare_event`/`_insert_event` path) gets a sequence number like any event; the export filters it out by rule, not by sequence.

### Self-heal of NULL sequences (code-only rollback, then re-upgrade)

If v12 code is rolled back without the schema, the v11 code inserts rows with `ingest_seq` NULL. After a re-upgrade, `_m12` is already recorded and would not run again, so those rows would never be exported. Therefore the same backfill statement (only rows `WHERE ingest_seq IS NULL`, numbered from `MAX(ingest_seq) + 1` in `(recorded_at, rowid)` order) runs **once at every startup**, right after `run_migrations`, inside the migration transaction. With no NULL rows it is a single indexed-miss scan (cheap at prod size). Such rows land at the end of the sequence; a consumer sees them in the next export, which is the documented "late data" behaviour.

### Indexes and Constraints

- `ux_token_events_ingest_seq` (unique, partial): keyset `ingest_seq > :after AND ingest_seq <= :as_of ORDER BY ingest_seq`, and `SELECT MAX(ingest_seq)` in O(log n).
- No other new index. Range filters on `COALESCE(occurred_at, recorded_at)` scan within the sequence range; acceptable at prod size (~10^4 events) and bounded by the export's 92-day range limit (backend.md X2). Revisit only if the Verification Log shows an export page above 1 s.

### Data Backfill

Existing rows get 1..N in `(recorded_at, rowid)` order inside `_m12`. It is a one-off ordering for history only; the snapshot guarantee applies to rows ingested after v12.

### Migration Strategy

- Forward: one transaction: `ADD COLUMN` (O(1)), one full-table `UPDATE` (prod ~10^4 rows: well under a second), unique index build. Automatic verified backup first.
- Rollback safety: code-only rollback is safe (column nullable, index partial). Re-upgrade after a code-only rollback is covered by the startup self-heal. Schema rollback via `scripts/rollback_v12.sql`.
- Lock implications: runs at startup before serving. At runtime the `MAX()+1` subquery adds one index lookup per insert under the write lock that the insert already holds; batch ingest (≤ 500 events per transaction) is unaffected in shape.

### Acceptance Criteria (Phase 3)

- AC-DB3.1 (supports AC3.4): v12 applies cleanly on a fresh DB; fresh and migrated `PRAGMA table_info(token_events)` agree.
- AC-DB3.2: v12 applies cleanly on a v11 DB with events; every existing row gets a distinct `ingest_seq` 1..N in `(recorded_at, rowid)` order; the pre-migration backup verifies.
- AC-DB3.3 (supports AC3.4): new inserts get `MAX + 1`; under concurrent batch ingest the sequence is gap-free, unique and in commit order (tests-other X-tests).
- AC-DB3.4: after rows are inserted with `ingest_seq` NULL (simulated old code), a restart assigns them `MAX + 1 …` in `(recorded_at, rowid)` order; a second restart changes nothing.
- AC-DB3.5: `scripts/rollback_v12.sql` restores the v11 column set; v11 code inserts on a v12 DB.

## Out of Scope

- A jobs table, a job-duration column or any job-level launcher event (brief: out of scope).
- New `token_events` columns for `job_ref`, `runtime`, `work_type`, `job_attempt` or `producer` (they stay in `tags_json`).
- Storing `parent_session_id`, `parent_turn_id` or `parent_project_name` on `token_events` (the repair script re-derives parents by hash matching instead — backend.md J7).
- Any change to the O10 prompt columns, retention or JEV tables.
