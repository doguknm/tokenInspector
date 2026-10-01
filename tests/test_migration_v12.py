"""O9 schema v12: token_events.ingest_seq, export_state high-water, self-heal, rollback runner (database.md AC-DB3.x)."""

import asyncio
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import text

import database
import migrations
from scripts import repair_task_parents
from scripts import rollback_schema
from test_job_correlation import event, post, rows
from test_repair_task_parents import build, main_rows

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


def _seqs(path: Path) -> dict[str, int | None]:
    with closing(sqlite3.connect(path)) as conn:
        return dict(conn.execute("SELECT id, ingest_seq FROM token_events"))


def _state(path: Path) -> tuple:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT last_seq, revision, epoch FROM export_state").fetchone()


def _insert_old_shape(conn, event_id, recorded_at, project="hermes", client_event_id=None):
    """An INSERT as code before v12 writes it: it never names ingest_seq."""
    conn.execute(f"INSERT INTO token_events ({EVENT_COLS}) VALUES (?, ?, ?, ?, 'llm_request', 'm', 0, 0, 0, 0, 0, "
                 "'success', 1, 0, 'unpriced')", (event_id, project, client_event_id, recorded_at))


async def _v11_db(tmp_path, rows, name="v11.db") -> Path:
    """A genuine v11 DB: built by the app, rolled back by the runner, then filled with v11-shaped rows."""
    path = tmp_path / name
    await database.init_db(str(path))
    assert rollback_schema.main(["--db", str(path), "--to", "11"]) == 0
    assert _version(path) == 11 and "ingest_seq" not in [c[1] for c in _columns(path, "token_events")]
    with closing(sqlite3.connect(path)) as conn:
        for row in rows:
            _insert_old_shape(conn, *row)
        conn.commit()
    return path


# Same recorded_at for e-b and e-c: their order falls back to rowid (insertion order).
UNORDERED = [("e-late", "2026-09-03T00:00:00Z"), ("e-b", "2026-09-02T00:00:00Z"),
             ("e-c", "2026-09-02T00:00:00Z"), ("e-early", "2026-09-01T00:00:00Z")]


@needs_drop_column
async def test_m12_fresh_equals_migrated(tmp_path):
    fresh = tmp_path / "fresh.db"
    await database.init_db(str(fresh))
    migrated = await _v11_db(tmp_path, UNORDERED)
    await database.init_db(str(migrated))
    assert _version(fresh) == _version(migrated) == 12 == migrations.LATEST_SCHEMA_VERSION
    for table in ("tasks", "token_events", "export_state"):
        assert _columns(fresh, table) == _columns(migrated, table), table
        assert _indexes(fresh, table) == _indexes(migrated, table), table
    with closing(sqlite3.connect(fresh)) as f, closing(sqlite3.connect(migrated)) as m:
        query = "SELECT sql FROM sqlite_master WHERE name IN ('ux_token_events_ingest_seq', 'export_state') ORDER BY name"
        assert f.execute(query).fetchall() == m.execute(query).fetchall()
    last_seq, revision, epoch = _state(fresh)
    assert (last_seq, revision) == (0, 0) and re.fullmatch(r"[0-9a-f]{32}", epoch)
    assert _state(migrated)[2] != epoch  # a random epoch per created row


@needs_drop_column
async def test_m12_backfill_order(tmp_path):
    path = await _v11_db(tmp_path, UNORDERED)
    await database.init_db(str(path))
    assert _seqs(path) == {"e-early": 1, "e-b": 2, "e-c": 3, "e-late": 4}
    assert _state(path)[:2] == (4, 0)
    (backup,) = [p for p in tmp_path.glob("v11.db.bak-v11-*") if not p.name.endswith(("-wal", "-shm", "-journal"))]
    migrations.verify_backup(path, backup, 11)
    with closing(sqlite3.connect(path)) as conn:  # the partial unique index enforces distinct sequences
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE token_events SET ingest_seq = 1 WHERE id = 'e-late'")


async def test_null_seq_self_heal_on_startup(tmp_path):
    path = tmp_path / "v12.db"
    await database.init_db(str(path))
    with closing(sqlite3.connect(path)) as conn:
        _insert_old_shape(conn, "a", "2026-09-01T00:00:00Z")
        conn.commit()
    await database.init_db(str(path))
    assert _seqs(path) == {"a": 1} and _state(path)[0] == 1
    with closing(sqlite3.connect(path)) as conn:  # v11 code keeps inserting on a v12 DB (NULL ingest_seq)
        _insert_old_shape(conn, "old-2", "2026-09-05T00:00:00Z")
        _insert_old_shape(conn, "old-1", "2026-08-01T00:00:00Z")  # earlier time: numbered first, still above 1
        conn.commit()
    epoch = _state(path)[2]
    await database.init_db(str(path))
    assert _seqs(path) == {"a": 1, "old-1": 2, "old-2": 3}
    assert _state(path) == (3, 0, epoch)
    await database.init_db(str(path))  # a second restart changes nothing
    assert _seqs(path) == {"a": 1, "old-1": 2, "old-2": 3} and _state(path) == (3, 0, epoch)


async def test_heal_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "v12.db"
    await database.init_db(str(path))
    with closing(sqlite3.connect(path)) as conn:
        for n in range(3):
            _insert_old_shape(conn, f"old-{n}", f"2026-09-0{n + 1}T00:00:00Z")
        conn.commit()
    real = database.assign_missing_ingest_seq

    async def fails_after_numbering(conn):
        await real(conn)  # every row numbered and last_seq advanced inside the transaction ...
        raise RuntimeError("injected")  # ... then startup fails

    monkeypatch.setattr(database, "assign_missing_ingest_seq", fails_after_numbering)
    with pytest.raises(RuntimeError, match="injected"):
        await database.init_db(str(path))
    assert set(_seqs(path).values()) == {None} and _state(path)[0] == 0
    monkeypatch.setattr(database, "assign_missing_ingest_seq", real)
    await database.init_db(str(path))
    assert _seqs(path) == {"old-0": 1, "old-1": 2, "old-2": 3} and _state(path)[0] == 3


@needs_drop_column
async def test_rollback_runner_guards(tmp_path, monkeypatch, capsys):
    path = tmp_path / "v12.db"
    await database.init_db(str(path))
    with closing(sqlite3.connect(path)) as conn:
        _insert_old_shape(conn, "a", "2026-09-01T00:00:00Z")
        conn.commit()
    await database.init_db(str(path))

    # the exact source-version guard is re-read under the write lock: a version that moved is refused
    real = rollback_schema._version
    calls = []

    def moving(conn):
        calls.append(1)
        return real(conn) if len(calls) == 1 else 11

    monkeypatch.setattr(rollback_schema, "_version", moving)
    assert rollback_schema.main(["--db", str(path), "--to", "11"]) == 1
    monkeypatch.setattr(rollback_schema, "_version", real)
    assert _version(path) == 12 and "ingest_seq" in [c[1] for c in _columns(path, "token_events")]

    # SQLite < 3.35 is refused before any write
    monkeypatch.setattr(rollback_schema.sqlite3, "sqlite_version_info", (3, 34, 1))
    assert rollback_schema.main(["--db", str(path), "--to", "11"]) == 1
    monkeypatch.undo()
    assert _version(path) == 12
    capsys.readouterr()

    # --to 10 from v12 runs v12 -> 11, then 11 -> 10, in that order
    assert rollback_schema.main(["--db", str(path), "--to", "10"]) == 0
    out = capsys.readouterr().out
    assert out.index("12 -> 11") < out.index("11 -> 10") and "LATEST_SCHEMA_VERSION is 10" in out
    assert _version(path) == 10
    assert "ingest_seq" not in [c[1] for c in _columns(path, "token_events")] and not _columns(path, "export_state")


@needs_drop_column
async def test_rollback_to_v11_and_reupgrade(tmp_path, capsys):
    path = tmp_path / "v12.db"
    await database.init_db(str(path))
    old_epoch = _state(path)[2]
    assert rollback_schema.main(["--db", str(path), "--to", "11"]) == 0
    out = capsys.readouterr().out
    assert "12 -> 11" in out and "LATEST_SCHEMA_VERSION is 11" in out
    assert _version(path) == 11 and not _columns(path, "export_state")
    assert not {"ux_token_events_ingest_seq"} & {i[0] for i in _indexes(path, "token_events")}
    with closing(sqlite3.connect(path)) as conn:  # v11 code inserts on the rolled-back DB
        _insert_old_shape(conn, "v11-row", "2026-09-01T00:00:00Z")
        conn.commit()
    await database.init_db(str(path))  # re-upgrade: v12 again, a new epoch, the v11 row numbered
    assert _version(path) == 12 and _seqs(path) == {"v11-row": 1}
    assert _state(path)[2] != old_epoch


# --- X1: assignment on insert (AC-DB3.3, AC-DB3.7) ------------------------------------------------------------


async def _high_water() -> int:
    return (await rows("SELECT last_seq FROM export_state"))[0][0]


async def test_ingest_seq_monotonic_in_commit_order(client):
    base = await _high_water()
    batches = [[event(f"b{b}-{i}", session=f"s{b}", turn=f"t{i}") for i in range(5)] for b in range(20)]
    results = await asyncio.gather(*(post(client, *batch) for batch in batches))
    assert all(r["inserted"] == 5 for r in results)
    got = dict(await rows("SELECT client_event_id, ingest_seq FROM token_events"))
    seqs = sorted(got.values())
    assert seqs == list(range(base + 1, base + 101))  # unique, gap-free, above the old high-water
    for b in range(20):  # one batch = one transaction: its rows are contiguous and in request order
        mine = [got[f"b{b}-{i}"] for i in range(5)]
        assert mine == list(range(mine[0], mine[0] + 5))
    assert await _high_water() == base + 100
    again = await post(client, batches[0][0], event("fresh", session="s99"))  # a duplicate consumes no number
    assert again["duplicates"] == 1 and await _high_water() == base + 101
    assert (await rows("SELECT ingest_seq FROM token_events WHERE client_event_id = 'fresh'"))[0][0] == base + 101
    listed = (await client.get("/api/events")).json()["items"]
    assert listed and all("ingest_seq" not in item for item in listed)  # never returned by the listing


async def test_ingest_seq_not_reused_after_delete(client):
    await post(client, *(event(f"d{i}", turn=f"t{i}") for i in range(4)))
    as_of = await _high_water()
    async with database.AsyncSessionLocal() as session:  # maintenance deletes the highest rows
        await session.execute(text(
            "DELETE FROM token_events WHERE ingest_seq > :cut"), {"cut": as_of - 2})
        await session.commit()
    await post(client, event("after-1", turn="x1"), event("after-2", turn="x2"))
    new = [s for (s,) in await rows("SELECT ingest_seq FROM token_events WHERE client_event_id LIKE 'after-%'")]
    assert sorted(new) == [as_of + 1, as_of + 2]


async def _revision() -> int:
    return (await rows("SELECT revision FROM export_state"))[0][0]


async def test_revision_bumps(client, tmp_path, capsys):
    await post(client, event("r1", model="zz-revision-model"))
    start = await _revision()
    assert (await client.post("/api/settings/recost", params={"dry_run": "true"})).json()["changed"] == 0
    assert (await client.post("/api/settings/recost", params={"dry_run": "false"})).json()["changed"] == 0
    await post(client, event("r2", model="zz-revision-model"))  # ingest never bumps
    assert await _revision() == start
    rule = {"model": "zz-revision-model", "input_price_per_1m": 1.0, "output_price_per_1m": 2.0}
    assert (await client.post("/api/settings/pricing", json=rule)).status_code == 200
    assert (await client.post("/api/settings/recost", params={"dry_run": "true"})).json()["changed"] == 2
    assert await _revision() == start  # dry-run: no bump
    assert (await client.post("/api/settings/recost", params={"dry_run": "false"})).json()["changed"] == 2
    assert await _revision() == start + 1

    path = await build(tmp_path / "repair.sqlite", main_rows())
    with closing(sqlite3.connect(path)) as conn:
        start = conn.execute("SELECT revision FROM export_state").fetchone()[0]
    assert repair_task_parents.main(["--db", str(path)]) == 0  # dry-run
    assert _state(path)[1] == start
    assert repair_task_parents.main(["--db", str(path), "--apply"]) == 0  # relinks rows
    assert _state(path)[1] == start + 1
    assert repair_task_parents.main(["--db", str(path), "--apply"]) == 0  # nothing left to change
    assert _state(path)[1] == start + 1
    capsys.readouterr()
