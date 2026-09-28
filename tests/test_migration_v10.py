"""Schema v10: tasks + evaluator tables, backfill, backup and migration atomicity (database lane)."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import text

import database
import migrations
import task_store
from scripts import rollback_schema
from database import AsyncSessionLocal

FIXTURES = Path(__file__).parent / "fixtures"
NEW_TABLES = (
    "tasks",
    "evaluator_runs",
    "task_evaluations",
    "evaluator_attempts",
    "provider_cooldowns",
    "retention_state",
)
NEW_COLUMNS = tuple(migrations._M10_TOKEN_EVENT_COLUMNS)
RS1 = '{"complexity_version": "request-shape-v1", "complexity_source": "deterministic_metadata"}'


def _event(event_id, **values):
    row = {
        "id": event_id,
        "project_name": "hermes",
        "recorded_at": "2026-09-20T10:00:00.000000Z",
        "occurred_at": "2026-09-20T10:00:00.000000Z",
        "event_type": "llm_request",
        "model": "m",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "status": "success",
        "attempt": 1,
        "retry_count": 0,
        "cost_status": "unpriced",
        "session_id": "s1",
        "task_id": "task-1",
        "turn_id": "s1:task-1:aaaa0001",
    }
    row.update(values)
    return row


def _make_v9_db(path: Path, events=()) -> Path:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript((FIXTURES / "schema_v9.sql").read_text(encoding="utf-8"))
        conn.executemany(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, '2026-09-01T00:00:00.000000Z')",
            [(v,) for v in range(1, 10)],
        )
        for row in events:
            cols = ", ".join(row)
            conn.execute(f"INSERT INTO token_events ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
        conn.commit()
    return path


def _tables(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(path: Path, table: str) -> list[tuple]:
    with closing(sqlite3.connect(path)) as conn:
        return [tuple(r) for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]


def _rows(path: Path, table: str, key: str = "id") -> dict:
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return {r[key]: dict(r) for r in conn.execute(f"SELECT * FROM {table}")}


def _assert_v9_intact(path: Path) -> None:
    assert _version(path) == 9
    assert not set(NEW_TABLES) & _tables(path)
    assert not set(NEW_COLUMNS) & {c[1] for c in _columns(path, "token_events")}


def _index_sql(path: Path) -> dict[str, str]:
    with closing(sqlite3.connect(path)) as conn:
        return {
            name: sql
            for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")
        }


# --- AC3a / AC3g / AC3c -----------------------------------------------------------------


async def test_fresh_db_is_v10_with_all_tables_and_partial_indexes(tmp_path):
    path = tmp_path / "fresh.db"
    await database.init_db(str(path))
    assert _version(path) == migrations.LATEST_SCHEMA_VERSION
    assert set(NEW_TABLES) <= _tables(path)
    assert set(NEW_COLUMNS) <= {c[1] for c in _columns(path, "token_events")}
    indexes = _index_sql(path)
    assert "WHERE prompt_text IS NOT NULL" in indexes["ix_tasks_prompt_expires"]
    assert "WHERE status IN ('queued','running')" in indexes["ux_evaluator_runs_active"]
    assert "WHERE evaluator = 'jev' AND status = 'ok'" in indexes["ux_task_evaluations_jev_ok"]
    assert "WHERE evaluator = 'human'" in indexes["ux_task_evaluations_human"]
    assert "UNIQUE" in indexes["ux_tasks_project_session_turn"]
    assert "ix_token_events_project_session_turn" in indexes
    retention = {c[1] for c in _columns(path, "retention_state")}
    cooldowns = {c[1] for c in _columns(path, "provider_cooldowns")}
    assert retention == {"key", "value", "updated_at"}
    assert cooldowns == {"provider", "not_before", "reason", "updated_at"}


async def test_migrated_v9_keeps_every_row_and_matches_fresh_schema(tmp_path):
    events = [
        _event("e1", complexity=3, tags_json=RS1, prompt_hash="h", finish_reason="stop"),
        _event("e2", event_type="tool_call", tool_name="read_file", occurred_at="2026-09-20T10:00:01.000000Z"),
        _event("e3", event_type="session", finish_reason="end", turn_id="task-1", task_id="task-1"),
        _event("e4", project_name="docrag", session_id=None, task_id=None, turn_id=None, total_tokens=7),
    ]
    migrated = _make_v9_db(tmp_path / "v9.db", events)
    _assert_v9_intact(migrated)
    before = _rows(migrated, "token_events")

    await database.init_db(str(migrated))

    after = _rows(migrated, "token_events")
    assert set(after) == set(before)
    for event_id, row in before.items():
        assert {k: after[event_id][k] for k in row} == row  # every v9 column unchanged
        assert all(after[event_id][c] is None for c in NEW_COLUMNS)

    fresh = tmp_path / "fresh.db"
    await database.init_db(str(fresh))
    fresh_sql, migrated_sql = _index_sql(fresh), _index_sql(migrated)
    with closing(sqlite3.connect(fresh)) as f, closing(sqlite3.connect(migrated)) as m:
        for table in NEW_TABLES:
            query = "SELECT sql FROM sqlite_master WHERE type='table' AND name=?"
            assert f.execute(query, (table,)).fetchone() == m.execute(query, (table,)).fetchone()
        for table in (*NEW_TABLES, "token_events"):
            assert _columns(fresh, table) == _columns(migrated, table), table
            assert sorted(tuple(r)[1:] for r in f.execute(f'PRAGMA index_list("{table}")')) == sorted(
                tuple(r)[1:] for r in m.execute(f'PRAGMA index_list("{table}")')
            ), table
    for name in ("ix_token_events_project_session_turn", "ix_tasks_prompt_expires", "ux_evaluator_runs_active"):
        assert fresh_sql[name] == migrated_sql[name]


# --- AC3d ------------------------------------------------------------------------------


def test_backup_uses_online_api_and_includes_wal_rows(tmp_path):
    path = _make_v9_db(tmp_path / "prod.db", [_event("e1")])
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO token_events (id, project_name, recorded_at, event_type, model, prompt_tokens, "
                       "completion_tokens, cache_read_tokens, cache_creation_tokens, reasoning_tokens, status, attempt, "
                       "retry_count, cost_status) VALUES ('wal-row', 'hermes', 'x', 'llm_request', 'm', 0, 0, 0, 0, 0, "
                       "'success', 1, 0, 'unpriced')")
        writer.commit()
        assert Path(f"{path}-wal").stat().st_size > 0
        target = migrations.backup_database_if_needed(str(path))
    finally:
        writer.close()
    assert target is not None and target.name.startswith("prod.db.bak-v9-")
    with closing(sqlite3.connect(target)) as bak:
        assert bak.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert bak.execute("SELECT COUNT(*) FROM token_events WHERE id='wal-row'").fetchone()[0] == 1


async def test_failed_backup_verification_aborts_before_any_mutation(tmp_path, monkeypatch):
    path = _make_v9_db(tmp_path / "prod.db", [_event("e1")])
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    original = path.read_bytes()

    def broken(source, target, current):
        raise RuntimeError("pre-migration backup verification failed: simulated")

    monkeypatch.setattr(migrations, "verify_backup", broken)
    with pytest.raises(RuntimeError, match="backup verification failed"):
        await database.init_db(str(path))
    _assert_v9_intact(path)
    assert path.read_bytes() == original


def test_verify_backup_rejects_a_short_backup(tmp_path):
    source = _make_v9_db(tmp_path / "prod.db", [_event("e1"), _event("e2")])
    short = _make_v9_db(tmp_path / "short.db", [_event("e1")])
    with pytest.raises(RuntimeError, match="token_events 1 != 2"):
        migrations.verify_backup(source, short, 9)


async def test_backup_skip_conditions(tmp_path):
    assert migrations.backup_database_if_needed(str(tmp_path / "missing.db")) is None
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    assert migrations.backup_database_if_needed(str(empty)) is None
    current = tmp_path / "current.db"
    await database.init_db(str(current))
    assert migrations.backup_database_if_needed(str(current)) is None


# --- AC3f ------------------------------------------------------------------------------


async def test_failure_after_create_all_rolls_back_everything(tmp_path, monkeypatch):
    path = _make_v9_db(tmp_path / "prod.db", [_event("e1")])

    async def fail(conn):
        raise RuntimeError("injected before _m10")

    monkeypatch.setattr(database, "run_migrations", fail)
    with pytest.raises(RuntimeError, match="injected before _m10"):
        await database.init_db(str(path))
    _assert_v9_intact(path)  # create_all's new tables were rolled back too
    monkeypatch.undo()
    await database.init_db(str(path))
    assert _version(path) == migrations.LATEST_SCHEMA_VERSION


async def test_failure_inside_m10_rolls_back_partial_ddl(tmp_path, monkeypatch):
    path = _make_v9_db(tmp_path / "prod.db", [_event("e1")])

    async def partial_m10(conn):
        await conn.execute(text("CREATE TABLE IF NOT EXISTS tasks_probe (id TEXT)"))
        await migrations._add_columns(conn, "token_events", dict(list(migrations._M10_TOKEN_EVENT_COLUMNS.items())[:3]))
        raise RuntimeError("injected inside _m10")

    patched = [(v, partial_m10 if v == 10 else fn) for v, fn in migrations.MIGRATIONS]
    monkeypatch.setattr(migrations, "MIGRATIONS", patched)
    with pytest.raises(RuntimeError, match="injected inside _m10"):
        await database.init_db(str(path))
    _assert_v9_intact(path)
    assert "tasks_probe" not in _tables(path)
    monkeypatch.undo()
    await database.init_db(str(path))
    assert _version(path) == migrations.LATEST_SCHEMA_VERSION


# --- AC3e (rollback SQL) ---------------------------------------------------------------


async def test_rollback_sql_restores_v9_token_events_schema(tmp_path):
    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip("ALTER TABLE DROP COLUMN needs SQLite 3.35+")
    pinned = _make_v9_db(tmp_path / "pinned.db")
    path = _make_v9_db(tmp_path / "prod.db", [_event("e1")])
    await database.init_db(str(path))
    # v11+ is rolled back first through the O9 runner; rollback_v10.sql only ever runs on a v10 DB.
    assert rollback_schema.main(["--db", str(path), "--to", "10"]) == 0
    rollback = (Path(__file__).parent.parent / "scripts" / "rollback_v10.sql").read_text(encoding="utf-8")
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(rollback)
    _assert_v9_intact(path)
    assert _columns(path, "token_events") == _columns(pinned, "token_events")


# --- AC4a / AC4b / AC4d / AC4e / AC4f (backfill) ---------------------------------------


def _ts(second: int) -> str:
    return f"2026-09-20T10:00:{second:02d}.000000Z"


async def test_backfill_creates_one_metadata_only_task_per_turn(tmp_path):
    t1, t2, t3 = "s1:gw:aaaa0001", "s1:gw:aaaa0002", "s2:u2:bbbb0001"
    events = [
        _event("a1", turn_id=t1, task_id="gw", occurred_at=_ts(1), complexity=4, tags_json=RS1),
        _event("a2", turn_id=t1, task_id="gw", occurred_at=_ts(5), event_type="tool_call"),
        _event("a3", turn_id=t2, task_id="gw", occurred_at=_ts(10), complexity=2, tags_json=RS1),
        _event("a4", turn_id="gw", task_id="gw", occurred_at=_ts(20), event_type="session", finish_reason="end"),
        _event("b1", turn_id=t3, task_id="u2", session_id="s2", occurred_at=_ts(3)),
        _event("x1", turn_id="unknown", occurred_at=_ts(4)),
        _event("x2", turn_id=None, task_id=None, occurred_at=_ts(4)),
    ]
    path = _make_v9_db(tmp_path / "prod.db", events)
    await database.init_db(str(path))
    tasks = _rows(path, "tasks")
    assert len(tasks) == 3
    first = tasks[task_store.task_ref("hermes", "s1", t1)]
    second = tasks[task_store.task_ref("hermes", "s1", t2)]
    other = tasks[task_store.task_ref("hermes", "s2", t3)]
    assert first["id"] == hashlib.sha256("hermes\x1fs1\x1f".encode() + t1.encode()).hexdigest()[:32]
    assert (first["first_seen_at"], first["last_seen_at"]) == (_ts(1), _ts(5))
    assert first["source_task_id"] == "gw" and first["session_id"] == "s1" and first["turn_id"] == t1
    assert (first["start_complexity"], first["start_complexity_method"]) == (4, "request-shape-v1")
    assert (first["start_complexity_event_at"], first["start_complexity_event_id"]) == (_ts(1), "a1")
    assert (first["completion"], first["completed_at"]) == ("next_task", _ts(10))
    assert (second["completion"], second["completed_at"]) == ("session_end", _ts(20))
    assert (other["completion"], other["start_complexity"]) == (None, None)
    for task in tasks.values():
        assert (task["hierarchy_status"], task["source"]) == ("unknown", "backfill")
        assert task["parent_task_ref"] is None and task["root_task_ref"] is None
        for column in ("prompt_text", "prompt_hash", "prompt_length", "prompt_redaction_version",
                       "prompt_captured_at", "prompt_expires_at", "prompt_purged_at"):
            assert task[column] is None
        assert task["prompt_truncated"] == 0


async def test_backfill_is_idempotent(tmp_path):
    events = [
        _event("a1", turn_id="s1:t:1", occurred_at=_ts(1), complexity=3, tags_json=RS1),
        _event("a2", turn_id="s1:t:2", occurred_at=_ts(2)),
    ]
    path = _make_v9_db(tmp_path / "prod.db", events)
    await database.init_db(str(path))
    before = _rows(path, "tasks")
    await database.init_db(str(path))
    engine = database._migration_engine(str(path))
    try:
        async with engine.begin() as conn:
            assert await task_store.backfill_tasks(conn, "2026-09-27T00:00:00.000000Z") == 0
    finally:
        await engine.dispose()
    assert _rows(path, "tasks") == before


async def test_same_timestamp_tie_breaks_on_lowest_event_id(tmp_path):
    events = [
        _event("b-event", turn_id="s1:t:1", occurred_at=_ts(1), complexity=2, tags_json=RS1),
        _event("a-event", turn_id="s1:t:1", occurred_at=_ts(1), complexity=4, tags_json=RS1),
    ]
    path = _make_v9_db(tmp_path / "prod.db", events)
    await database.init_db(str(path))
    (task,) = _rows(path, "tasks").values()
    assert (task["start_complexity"], task["start_complexity_event_id"]) == (4, "a-event")


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ([("ai-v0", 5), ("request-shape-v1", 2)], (2, "request-shape-v1")),
        ([("request-shape-v1", 3), ("ai-v0", 1)], (3, "request-shape-v1")),
        ([(None, 4), ("request-shape-v1", 2)], (2, "request-shape-v1")),
        ([("ai-v0", 4), (None, 2)], (None, None)),
    ],
)
async def test_backfill_uses_only_approved_complexity_methods(tmp_path, sequence, expected):
    events = []
    for second, (method, complexity) in enumerate(sequence, start=1):
        tags = f'{{"complexity_version": "{method}"}}' if method else None
        events.append(_event(f"e{second}", turn_id="s1:t:1", occurred_at=_ts(second), complexity=complexity, tags_json=tags))
    path = _make_v9_db(tmp_path / "prod.db", events)
    await database.init_db(str(path))
    (task,) = _rows(path, "tasks").values()
    assert (task["start_complexity"], task["start_complexity_method"]) == expected


def test_complexity_method_column_takes_precedence_over_tags():
    assert task_store.complexity_method_of("ai-v0", RS1) == "ai-v0"
    assert task_store.start_complexity_candidate("llm_request", 3, "ai-v0", RS1) is None
    assert task_store.start_complexity_candidate("tool_call", 3, None, RS1) is None
    assert task_store.start_complexity_candidate("llm_request", 3, None, "not json") is None


async def test_completion_is_independent_of_insert_order(tmp_path):
    events = [
        _event("a", turn_id="s1:t:1", occurred_at=_ts(1)),
        _event("b", turn_id="s1:t:2", occurred_at=_ts(2)),
        _event("c", turn_id="s1:t:3", occurred_at=_ts(3)),
        _event("m", turn_id="t", occurred_at=_ts(9), event_type="session", finish_reason="shutdown"),
        _event("n", turn_id="t", occurred_at=_ts(8), event_type="session", finish_reason="start"),
    ]
    results = []
    for name, order in (("forward", events), ("reversed", list(reversed(events)))):
        path = _make_v9_db(tmp_path / f"{name}.db", order)
        await database.init_db(str(path))
        results.append({t["turn_id"]: (t["completion"], t["completed_at"]) for t in _rows(path, "tasks").values()})
    assert results[0] == results[1] == {
        "s1:t:1": ("next_task", _ts(2)),
        "s1:t:2": ("next_task", _ts(3)),
        "s1:t:3": ("session_end", _ts(9)),
    }


# --- AC5a ------------------------------------------------------------------------------


async def test_service_connections_enable_secure_delete(client):
    async with AsyncSessionLocal() as session:
        assert (await session.execute(text("PRAGMA secure_delete"))).scalar_one() == 1
