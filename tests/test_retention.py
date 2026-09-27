"""Retention: purge, WAL checkpoint retry, backup re-purge, scheduling and the standalone script
(AC5d, AC5e, AC5g, AC5h)."""

import logging
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

import database
import features
import retention
from database import AsyncSessionLocal

TOKEN = "r" * 24
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "purge_task_prompts.py"


def _iso(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _sql(statement, **params):
    async with AsyncSessionLocal() as session:
        result = await session.execute(text(statement), params)
        await session.commit()
        return result


async def _rows(statement, **params):
    async with AsyncSessionLocal() as session:
        return [tuple(r) for r in (await session.execute(text(statement), params)).all()]


async def _seed():
    for ref, expires in (("a" * 32, _iso(-1)), ("b" * 32, _iso(5))):
        await _sql(
            "INSERT INTO tasks (id, project_name, session_id, turn_id, first_seen_at, last_seen_at, prompt_text, "
            "prompt_captured_at, prompt_expires_at, created_at, updated_at) VALUES (:id, 'hermes', 's', :id, :n, :n, "
            ":text, :n, :e, :n, :n)",
            id=ref, n=_iso(-31), text=f"PROMPT-{ref[0]}", e=expires,
        )
    for eid, when in (("old", _iso(-31)), ("new", _iso(-1))):
        await _sql(
            "INSERT INTO task_evaluations (id, task_ref, evaluator, rubric_version, status, label, labeler, note, "
            "http_attempts, evaluated_at) VALUES (:id, :t, 'human', 'difficulty-v0', 'ok', 1, :id, :note, 0, :at)",
            id=eid, t="b" * 32, note=f"NOTE-{eid}", at=when,
        )


async def test_dry_run_changes_nothing_and_real_purge_nulls_without_deleting(client, monkeypatch, caplog):
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    await _seed()
    auth = {"X-Ingest-Token": TOKEN}
    assert (await client.post("/api/tasks/purge-expired")).status_code == 401
    dry = (await client.post("/api/tasks/purge-expired", headers=auth)).json()
    assert dry["dry_run"] is True and dry["eligible"] == 1 and dry["purged"] == 0
    assert await _rows("SELECT COUNT(*) FROM tasks WHERE prompt_text IS NOT NULL") == [(2,)]
    caplog.set_level(logging.INFO)
    real = (await client.post("/api/tasks/purge-expired?dry_run=false", headers=auth)).json()
    assert (real["purged"], real["notes_purged"]) == (1, 1)
    assert await _rows("SELECT id, prompt_text IS NULL, prompt_purged_at IS NOT NULL FROM tasks ORDER BY id") == [
        ("a" * 32, 1, 1), ("b" * 32, 0, 0)]
    assert await _rows("SELECT id, note FROM task_evaluations ORDER BY id") == [("new", "NOTE-new"), ("old", None)]
    assert "PROMPT-" not in caplog.text and "NOTE-" not in caplog.text
    status = (await client.get("/api/tasks/retention-status")).json()
    assert status["last_purge_ok"] is True and status["pending_expired"] == 0
    assert status["retained_data_present"] is True and status["pending_wal_checkpoint"] is False


async def test_backups_are_repurged_and_vacuumed_but_wal_siblings_ignored(client):
    await _seed()
    db = Path(database.DB_PATH)
    backup = db.with_name(f"{db.name}.bak-v9-test")
    sibling = db.with_name(f"{db.name}.bak-v9-test-wal")
    try:
        with closing(sqlite3.connect(db)) as src, closing(sqlite3.connect(backup)) as dst:
            src.backup(dst)
        sibling.write_bytes(b"not a database")
        assert retention.backup_files(str(db)) == [backup]
        async with AsyncSessionLocal() as session:
            result = await retention.purge_expired(session, str(db))
        assert (result["backups_scanned"], result["backups_repurged"]) == (1, 1)
        assert b"PROMPT-a" not in backup.read_bytes()  # VACUUM left no free page with the text
        with closing(sqlite3.connect(backup)) as conn:
            assert conn.execute("SELECT prompt_text FROM tasks ORDER BY id").fetchall() == [(None,), ("PROMPT-b",)]
    finally:
        backup.unlink(missing_ok=True)
        sibling.unlink(missing_ok=True)


async def test_pending_checkpoint_is_retried_without_new_expiries(client, monkeypatch):
    await _seed()
    calls = []

    def busy_then_ok(path):
        calls.append(path)
        return len(calls) > 1

    monkeypatch.setattr(retention, "wal_checkpoint", busy_then_ok)
    async with AsyncSessionLocal() as session:
        first = await retention.purge_expired(session, database.DB_PATH)
        assert first["checkpoint_ok"] is False
        assert await retention.get_state(session, "pending_wal_checkpoint") == "1"
        second = await retention.purge_expired(session, database.DB_PATH)  # nothing new expires
        assert second["purged"] == 0 and second["checkpoint_ok"] is True
        assert await retention.get_state(session, "pending_wal_checkpoint") == "0"
    assert len(calls) == 2


async def test_failures_surface_in_status(client, monkeypatch):
    await _seed()
    await _sql("UPDATE tasks SET prompt_expires_at = :e WHERE id = :id", e=_iso(-2), id="b" * 32)

    def broken(*_args):
        raise OSError("disk")

    monkeypatch.setattr(retention, "wal_checkpoint", broken)
    async with AsyncSessionLocal() as session:
        with pytest.raises(OSError):
            await retention.purge_expired(session, database.DB_PATH)
    await _sql("UPDATE tasks SET prompt_text = 'still here' WHERE id = :id", id="b" * 32)
    status = (await client.get("/api/tasks/retention-status")).json()
    assert status["last_purge_ok"] is False and status["last_error"] == "OSError"
    assert status["oldest_overdue_expires_at"] is not None and status["pending_expired"] == 1


def _state(**env):
    return features.evaluate({"INGEST_TOKEN": TOKEN, "TASK_PROMPT_ALLOWED_PROJECTS": "hermes", **env})


@pytest.mark.parametrize(
    ("env", "retained", "verified", "expected"),
    [
        ({"STORE_TASK_PROMPTS": "1"}, False, None, (True, 21600, False)),
        ({"STORE_TASK_PROMPTS": "1", "TASK_PROMPT_PURGE_INTERVAL_S": "60"}, False, None, (True, 60, False)),
        ({}, False, None, (False, 21600, False)),
        ({}, True, None, (True, 21600, False)),
        ({"TASK_PROMPT_PURGE_INTERVAL_S": "0"}, True, "2026-09-27", (True, 21600, True)),
        ({"TASK_PROMPT_PURGE_INTERVAL_S": "0"}, False, None, (True, 21600, True)),
        ({"TASK_PROMPT_PURGE_INTERVAL_S": "0"}, False, "2026-09-27", (False, 0, False)),
        ({"TASK_PROMPT_PURGE_INTERVAL_S": "99999"}, True, None, (True, 21600, True)),
    ],
)
def test_schedule_runs_whenever_data_is_retained(env, retained, verified, expected):
    schedule = retention.decide_schedule(_state(**env), retained, verified)
    assert (schedule.scheduled, schedule.interval_s, schedule.interval_refused) == expected


def _make_db(path: Path, expired: bool) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, prompt_text TEXT, prompt_expires_at TEXT, "
            "prompt_purged_at TEXT, updated_at TEXT);"
            "CREATE TABLE task_evaluations (id TEXT PRIMARY KEY, evaluator TEXT, note TEXT, evaluated_at TEXT);"
            "CREATE TABLE retention_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL);"
        )
        conn.execute("INSERT INTO tasks VALUES ('t1', 'PROMPT-X', ?, NULL, NULL)", (_iso(-1 if expired else 5),))
        conn.execute("INSERT INTO task_evaluations VALUES ('e1', 'human', 'NOTE-X', ?)", (_iso(-1),))
        conn.commit()


def test_standalone_script_purges_all_copies_without_app_imports(tmp_path):
    db = tmp_path / "live.db"
    _make_db(db, expired=False)
    _make_db(tmp_path / "live.db.bak-v9-x", expired=False)
    run = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--db", str(db), "--all", "--include-backups", "--verify"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
    assert "PROMPT-X" not in run.stdout and "verified" in run.stdout
    for path in (db, tmp_path / "live.db.bak-v9-x"):
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("SELECT prompt_text FROM tasks").fetchall() == [(None,)]
            assert conn.execute("SELECT note FROM task_evaluations").fetchall() == [(None,)]
        assert b"PROMPT-X" not in path.read_bytes() or path == db
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT value FROM retention_state WHERE key='all_copy_purge_verified_at'").fetchone()


def test_standalone_script_expired_mode_and_dry_run(tmp_path):
    db = tmp_path / "live.db"
    _make_db(db, expired=True)
    dry = subprocess.run([sys.executable, "-I", str(SCRIPT), "--db", str(db), "--expired", "--dry-run"],
                         capture_output=True, text=True)
    assert "would purge 1" in dry.stdout
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT prompt_text FROM tasks").fetchone() == ("PROMPT-X",)
    subprocess.run([sys.executable, "-I", str(SCRIPT), "--db", str(db), "--expired"], check=True, capture_output=True)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT prompt_text, prompt_purged_at IS NOT NULL FROM tasks").fetchone() == (None, 1)
    bad = subprocess.run([sys.executable, "-I", str(SCRIPT), "--db", str(db), "--expired", "--verify"],
                         capture_output=True, text=True)
    assert bad.returncode == 2
