"""O9 schema v11: tasks.job_ref / job_ref_conflicts, cc- uniqueness index, rollback runner (database.md AC-DB1.x)."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import database
import migrations
from scripts import rollback_schema

V10_TASK_COLUMNS = [
    "id", "project_name", "session_id", "turn_id", "source_task_id", "parent_task_ref", "root_task_ref",
    "hierarchy_status", "source", "first_seen_at", "last_seen_at", "completion", "completed_at",
    "start_complexity", "start_complexity_method", "start_complexity_event_at", "start_complexity_event_id",
    "prompt_text", "prompt_hash", "prompt_length", "prompt_truncated", "prompt_redaction_version",
    "prompt_captured_at", "prompt_expires_at", "prompt_purged_at", "created_at", "updated_at"]
EVENT_COLS = ("id, project_name, client_event_id, recorded_at, event_type, model, prompt_tokens, completion_tokens, "
              "cache_read_tokens, cache_creation_tokens, reasoning_tokens, status, attempt, retry_count, cost_status")
needs_drop_column = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35, 0), reason="DROP COLUMN needs SQLite 3.35+")


def _columns(path: Path, table: str) -> list[tuple]:
    with closing(sqlite3.connect(path)) as conn:
        return [tuple(r) for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _indexes(path: Path, table: str) -> list[tuple]:
    with closing(sqlite3.connect(path)) as conn:
        return sorted(tuple(r)[1:] for r in conn.execute(f'PRAGMA index_list("{table}")'))


def _version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]


def _insert_event(conn, event_id, project, client_event_id):
    conn.execute(f"INSERT INTO token_events ({EVENT_COLS}) VALUES (?, ?, ?, '2026-09-28T00:00:00Z', 'llm_request', "
                 "'m', 0, 0, 0, 0, 0, 'success', 1, 0, 'unpriced')", (event_id, project, client_event_id))


def _insert_task_v10_shape(conn, task_id):
    """The v10 app's task INSERT: it never names the v11 columns."""
    conn.execute("INSERT INTO tasks (id, project_name, session_id, turn_id, hierarchy_status, source, first_seen_at, "
                 "last_seen_at, prompt_truncated, created_at, updated_at) VALUES (?, 'hermes', 's', ?, 'unknown', "
                 "'ingest', 'x', 'x', 0, 'x', 'x')", (task_id, task_id))


async def _v10_db(tmp_path, name="v10.db", seed=None) -> Path:
    """A genuine v10 DB: built by the app, then rolled back by the runner (create_all would add v11 columns)."""
    path = tmp_path / name
    await database.init_db(str(path))
    assert rollback_schema.main(["--db", str(path), "--to", "10"]) == 0
    assert [c[1] for c in _columns(path, "tasks")] == V10_TASK_COLUMNS and _version(path) == 10
    with closing(sqlite3.connect(path)) as conn:
        _insert_task_v10_shape(conn, "task-1")
        _insert_event(conn, "e1", "hermes", "c1")
        if seed:
            seed(conn)
        conn.commit()
    return path


@needs_drop_column
async def test_m11_fresh_equals_migrated(tmp_path):
    fresh = tmp_path / "fresh.db"
    await database.init_db(str(fresh))
    migrated = await _v10_db(tmp_path)
    await database.init_db(str(migrated))
    assert _version(fresh) == _version(migrated) == migrations.LATEST_SCHEMA_VERSION
    for table in ("tasks", "token_events"):
        assert _columns(fresh, table) == _columns(migrated, table), table
        assert _indexes(fresh, table) == _indexes(migrated, table), table
    with closing(sqlite3.connect(fresh)) as f, closing(sqlite3.connect(migrated)) as m:
        query = "SELECT sql FROM sqlite_master WHERE name IN ('ix_tasks_job_ref', 'ux_token_events_cc_client_event') ORDER BY name"
        assert f.execute(query).fetchall() == m.execute(query).fetchall()


@needs_drop_column
async def test_m11_on_v10_db(tmp_path):
    path = await _v10_db(tmp_path)
    await database.init_db(str(path))
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT job_ref, job_ref_conflicts FROM tasks").fetchall() == [(None, 0)]
        assert conn.execute("SELECT version FROM schema_migrations WHERE version = 11").fetchone() == (11,)
    (backup,) = [p for p in tmp_path.glob("v10.db.bak-v10-*") if not p.name.endswith(("-wal", "-shm", "-journal"))]
    migrations.verify_backup(path, backup, 10)


@needs_drop_column
async def test_m11_idempotent(tmp_path):
    path = await _v10_db(tmp_path)
    await database.init_db(str(path))
    before = (_columns(path, "tasks"), _indexes(path, "tasks"), _indexes(path, "token_events"))
    await database.init_db(str(path))
    engine = database._migration_engine(str(path))
    try:
        async with engine.begin() as conn:
            await migrations._m11(conn)  # a second run of the step itself is a no-op
    finally:
        await engine.dispose()
    assert (_columns(path, "tasks"), _indexes(path, "tasks"), _indexes(path, "token_events")) == before


@needs_drop_column
async def test_rollback_to_v10(tmp_path, capsys):
    path = tmp_path / "v11.db"
    await database.init_db(str(path))
    with closing(sqlite3.connect(path)) as conn:
        _insert_task_v10_shape(conn, "old-code")  # the v10 code path still inserts on a v11 DB
        conn.commit()
        assert conn.execute("SELECT job_ref, job_ref_conflicts FROM tasks").fetchall() == [(None, 0)]
    assert rollback_schema.main(["--db", str(path), "--to", "10"]) == 0
    assert [c[1] for c in _columns(path, "tasks")] == V10_TASK_COLUMNS and _version(path) == 10
    names = {i[0] for i in _indexes(path, "tasks") + _indexes(path, "token_events")}
    assert not {"ix_tasks_job_ref", "ux_token_events_cc_client_event"} & names
    out = capsys.readouterr().out
    assert "11 -> 10" in out and "LATEST_SCHEMA_VERSION is 10" in out
    # already at the target: nothing to do; re-upgrade restores v11
    assert rollback_schema.main(["--db", str(path), "--to", "10"]) == 0
    await database.init_db(str(path))
    assert _version(path) == migrations.LATEST_SCHEMA_VERSION


async def test_rollback_runner_refuses_unknown_source_and_old_sqlite(tmp_path, monkeypatch):
    path = tmp_path / "v11.db"
    await database.init_db(str(path))
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (13, 'x')")
        conn.commit()
    columns = _columns(path, "tasks")
    assert rollback_schema.main(["--db", str(path), "--to", "10"]) == 1  # no step from 13: refused
    assert _columns(path, "tasks") == columns and _version(path) == 13
    monkeypatch.setattr(rollback_schema.sqlite3, "sqlite_version_info", (3, 34, 1))
    assert rollback_schema.main(["--db", str(path), "--to", "10"]) == 1


@needs_drop_column
async def test_rollback_step_guard_rechecks_version_under_lock(tmp_path, monkeypatch, capsys):
    """The exact source-version check runs inside the step's write transaction."""
    path = tmp_path / "v11.db"
    await database.init_db(str(path))
    assert rollback_schema.main(["--db", str(path), "--to", "11"]) == 0
    assert _version(path) == 11
    capsys.readouterr()
    calls = []
    real = rollback_schema._version

    def moving(conn):
        calls.append({row[1] for row in conn.execute("PRAGMA table_info(tasks)")})  # columns at this read
        return real(conn) if len(calls) == 1 else 12  # changed between the plan and the step

    monkeypatch.setattr(rollback_schema, "_version", moving)
    assert rollback_schema.rollback(path, 10) == 1
    assert len(calls) == 2 and "job_ref" in calls[1]  # refused by the in-transaction guard, before any statement
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "rollback_schema: rollback 11 -> 10 failed; nothing from this step was applied\n"
    assert _version(path) == 11


# --- AC-DB1.5 / AC2.4: cc- ids unique across projects ---------------------------------------------------------


@pytest.mark.parametrize("origin", ["fresh", "migrated"])
async def test_cc_client_event_unique_across_projects(tmp_path, origin):
    if origin == "fresh":
        path = tmp_path / "v11.db"
    else:  # create_all also builds the index on a fresh DB; only a migrated DB proves _m11 creates it
        if sqlite3.sqlite_version_info < (3, 35, 0):
            pytest.skip("DROP COLUMN needs SQLite 3.35+")
        path = await _v10_db(tmp_path)
    await database.init_db(str(path))
    with closing(sqlite3.connect(path)) as conn:
        _insert_event(conn, "e1x", "a", "cc-0123")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_event(conn, "e2", "b", "cc-0123")
        _insert_event(conn, "e3", "a", "plugin-0123")
        _insert_event(conn, "e4", "b", "plugin-0123")  # other producers stay per project
        _insert_event(conn, "e5", "b", "CC-0123")  # prefix is case-sensitive (substr, not LIKE)
        conn.commit()


@needs_drop_column
async def test_m11_aborts_on_cross_project_cc_duplicates(tmp_path):
    def seed(conn):
        _insert_event(conn, "d1", "a", "cc-zz-secret-id")
        _insert_event(conn, "d2", "b", "cc-zz-secret-id")

    path = await _v10_db(tmp_path, seed=seed)
    with pytest.raises(RuntimeError) as info:
        await database.init_db(str(path))
    assert str(info.value) == migrations.CC_DUPLICATES_MESSAGE and "zz-secret" not in str(info.value)
    assert _version(path) == 10 and [c[1] for c in _columns(path, "tasks")] == V10_TASK_COLUMNS
