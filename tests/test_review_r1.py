"""Regression tests for the code-review round-1 findings (Plans/task-telemetry-jev-pilot/hermes/code-r1-triage.md)."""

import asyncio
import copy
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

import database
import features
import jev_scorer
import retention
import task_store
from database import AsyncSessionLocal
from main import app, lifespan

REPO = Path(__file__).resolve().parent.parent
TOKEN = "v" * 24
H = {"X-Project-Name": "hermes"}
START = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
OK_BODY = json.loads((Path(__file__).parent / "fixtures" / "jev_response_2026-09-27.json").read_text(encoding="utf-8"))[
    "response"]


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _rows(sql, **params):
    async with AsyncSessionLocal() as session:
        return [tuple(r) for r in (await session.execute(text(sql), params)).all()]


async def _exec(sql, **params):
    async with AsyncSessionLocal() as session:
        await session.execute(text(sql), params)
        await session.commit()


# --- P1-F3: descendant roots are reconciled when an ancestor arrives late ---------------------------------


async def test_grandchild_before_parent_gets_the_true_root(client):
    def ev(cid, session, turn, parent_session=None, parent_turn=None):
        body = {"client_event_id": cid, "model": "m", "session_id": session, "turn_id": turn,
                "occurred_at": "2026-09-20T10:00:00Z"}
        if parent_session:
            body.update(parent_session_id=parent_session, parent_turn_id=parent_turn)
        return body

    for body in (ev("c", "sc", "tc", "sb", "tb"), ev("a", "sa", "ta"), ev("b", "sb", "tb", "sa", "ta")):
        assert (await client.post("/api/events", json=body, headers=H)).status_code == 201
    root = task_store.task_ref("hermes", "sa", "ta")
    roots = dict(await _rows("SELECT turn_id, root_task_ref FROM tasks"))
    assert roots["tb"] == root and roots["tc"] == root


# --- P1-F4: :memory: initialises the service engine itself ------------------------------------------------


def test_in_memory_database_serves_after_startup(tmp_path):
    code = (
        "import asyncio, httpx\n"
        "from main import app, lifespan\n"
        "async def main():\n"
        "    async with lifespan(app):\n"
        "        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:\n"
        "            print((await c.get('/api/tasks')).status_code, (await c.get('/api/meta')).status_code)\n"
        "asyncio.run(main())\n"
    )
    # inherit the environment (user-site packages need APPDATA/HOME) but drop any feature switches
    env = {k: v for k, v in os.environ.items()
           if k not in {"STORE_TASK_PROMPTS", "JEV_ENABLED", "INGEST_TOKEN", "STORE_RAW_PROMPTS"}}
    env.update({"DB_PATH": ":memory:", "TOKEN_INSPECTOR_ALLOWED_HOSTS": "test", "STORE_RAW_PROMPTS": "0"})
    run = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-800:]
    assert run.stdout.strip().endswith("200 200")


# --- P5-F2: independent schema expectations ------------------------------------------------------------------


async def test_schema_matches_independent_expectations(tmp_path):
    path = tmp_path / "fresh.db"
    await database.init_db(str(path))
    with closing(sqlite3.connect(path)) as conn:
        tasks = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
        assert tasks == [
            "id", "project_name", "session_id", "turn_id", "source_task_id", "parent_task_ref", "root_task_ref",
            "hierarchy_status", "source", "first_seen_at", "last_seen_at", "completion", "completed_at",
            "start_complexity", "start_complexity_method", "start_complexity_event_at", "start_complexity_event_id",
            "prompt_text", "prompt_hash", "prompt_length", "prompt_truncated", "prompt_redaction_version",
            "prompt_captured_at", "prompt_expires_at", "prompt_purged_at", "created_at", "updated_at",
            "job_ref", "job_ref_conflicts"]
        events = [r[1] for r in conn.execute("PRAGMA table_info(token_events)")]
        assert events[-8:] == ["complexity_method", "request_system_chars", "request_history_chars",
                               "request_tool_output_chars", "request_file_content_chars", "request_file_ref_count",
                               "request_tool_names_json", "ingest_seq"]
        unique = conn.execute("SELECT \"unique\" FROM pragma_index_list('tasks') WHERE name='ux_tasks_project_session_turn'"
                              ).fetchone()[0]
        cols = [r[2] for r in conn.execute("PRAGMA index_info('ux_tasks_project_session_turn')")]
        assert unique == 1 and cols == ["project_name", "session_id", "turn_id"]
        lookup = [r[2] for r in conn.execute("PRAGMA index_info('ix_token_events_project_session_turn')")]
        assert lookup == ["project_name", "session_id", "turn_id"]
        human = [r[2] for r in conn.execute("PRAGMA index_info('ux_task_evaluations_human')")]
        assert human == ["task_ref", "rubric_version", "labeler"]


# --- P5-F3 / P5-F4: capture over the batch endpoint; first write keeps capture metadata ------------------------


def _capture_on(monkeypatch):
    monkeypatch.setenv("STORE_TASK_PROMPTS", "1")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "hermes")
    features.refresh()
    return {**H, "X-Ingest-Token": TOKEN}


def _ev(cid, turn, **extra):
    return {"client_event_id": cid, "model": "m", "session_id": "s1", "turn_id": turn,
            "occurred_at": "2026-09-20T10:00:00Z", "task_hierarchy": "root", "prompt_eligibility": "v1-allowed",
            **extra}


async def test_batch_capture_scrubs_discards_expired_and_respects_the_gate(client, monkeypatch):
    fresh = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    batch = {"events": [_ev("a", "t1", task_prompt_text="use sk-" + "x" * 20, task_prompt_captured_at=fresh),
                        _ev("b", "t2", task_prompt_text="old", task_prompt_captured_at=old)]}
    assert (await client.post("/api/events/batch", json=batch, headers=H)).json()["inserted"] == 2
    assert await _rows("SELECT COUNT(*) FROM tasks WHERE prompt_text IS NOT NULL") == [(0,)]  # gate off
    headers = _capture_on(monkeypatch)
    batch = {"events": [_ev("c", "t3", task_prompt_text="use sk-" + "x" * 20, task_prompt_captured_at=fresh),
                        _ev("d", "t4", task_prompt_text="old", task_prompt_captured_at=old)]}
    assert (await client.post("/api/events/batch", json=batch, headers=headers)).json()["inserted"] == 2
    prompts = dict(await _rows("SELECT turn_id, prompt_text FROM tasks"))
    assert prompts["t3"] == "use [REDACTED:secret]" and prompts["t4"] is None
    assert await _rows("SELECT COUNT(*) FROM token_events WHERE prompt_text IS NOT NULL") == [(0,)]
    features.refresh()


async def test_later_capture_never_extends_retention(client, monkeypatch):
    headers = _capture_on(monkeypatch)
    first = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    later = datetime.now(timezone.utc).isoformat()
    await client.post("/api/events", json=_ev("a", "t1", task_prompt_text="one", task_prompt_captured_at=first),
                      headers=headers)
    before = await _rows("SELECT prompt_text, prompt_captured_at, prompt_expires_at FROM tasks")
    await client.post("/api/events", json=_ev("b", "t1", task_prompt_text="two", task_prompt_captured_at=later),
                      headers=headers)
    assert await _rows("SELECT prompt_text, prompt_captured_at, prompt_expires_at FROM tasks") == before
    features.refresh()


# --- retention: P2-F3/F4/F5 and P6-F3 --------------------------------------------------------------------------


async def _seed_expired(ref="a" * 32, text_value="PROMPT-EXPIRED"):
    now = datetime.now(timezone.utc)
    await _exec("INSERT INTO tasks (id, project_name, session_id, turn_id, first_seen_at, last_seen_at, prompt_text, "
                "prompt_captured_at, prompt_expires_at, created_at, updated_at) VALUES (:id, 'hermes', 's', :id, :n, :n, "
                ":t, :n, :e, :n, :n)", id=ref, n=_iso(now - timedelta(days=31)), t=text_value,
                e=_iso(now - timedelta(days=1)))


async def test_busy_checkpoint_is_reported_as_an_incomplete_purge(client, monkeypatch):
    await _seed_expired()
    monkeypatch.setattr(retention, "wal_checkpoint", lambda path: False)
    async with AsyncSessionLocal() as session:
        result = await retention.purge_expired(session, database.DB_PATH)
    assert result["errors"] == ["checkpoint_busy"]
    status = (await client.get("/api/tasks/retention-status")).json()
    assert status["last_purge_ok"] is False and status["last_error"] == "checkpoint_busy"
    assert status["pending_wal_checkpoint"] is True


async def test_corrupt_backup_neither_blocks_live_purge_nor_hides_retained_data(client):
    db = Path(database.DB_PATH)
    corrupt = db.with_name(f"{db.name}.bak-v9-corrupt")
    corrupt.write_bytes(b"not a sqlite database at all" * 100)
    try:
        await _seed_expired()
        async with AsyncSessionLocal() as session:
            assert await retention.retained_data_present(session, str(db)) is True
            result = await retention.purge_expired(session, str(db))
        assert result["purged"] == 1 and any(e.startswith("backup_") for e in result["errors"])
        assert (await client.get("/api/tasks/retention-status")).json()["last_purge_ok"] is False
    finally:
        corrupt.unlink(missing_ok=True)


async def test_backup_physical_cleanup_is_retried_until_free_pages_are_gone(client):
    db = Path(database.DB_PATH)
    backup = db.with_name(f"{db.name}.bak-v9-retry")
    try:
        await _seed_expired(text_value="LEFTOVER-" + "z" * 20000)
        with closing(sqlite3.connect(db)) as src, closing(sqlite3.connect(backup)) as dst:
            src.backup(dst)
        with closing(sqlite3.connect(backup)) as conn:  # an earlier purge that crashed before VACUUM
            conn.execute("PRAGMA secure_delete=OFF")  # some builds (e.g. Debian) default it ON
            conn.execute("UPDATE tasks SET prompt_text = NULL")
            conn.commit()
            assert conn.execute("PRAGMA freelist_count").fetchone()[0] > 0
        assert b"LEFTOVER-" in backup.read_bytes()
        async with AsyncSessionLocal() as session:
            await retention.purge_expired(session, str(db))
        assert b"LEFTOVER-" not in backup.read_bytes()
    finally:
        backup.unlink(missing_ok=True)


async def test_startup_purges_retained_data_and_retries_a_pending_checkpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(retention, "wal_checkpoint", lambda path: calls.append(path) or True)
    async with lifespan(app):
        await _exec("DELETE FROM task_evaluations")
        await _exec("DELETE FROM tasks")
        await _seed_expired()
        await _exec("INSERT INTO retention_state (key, value, updated_at) VALUES ('pending_wal_checkpoint', '1', 'x') "
                    "ON CONFLICT(key) DO UPDATE SET value = '1'")
    calls.clear()
    async with lifespan(app):  # capture is off: the startup purge still runs
        assert await _rows("SELECT prompt_text FROM tasks") == [(None,)]
        assert await _rows("SELECT value FROM retention_state WHERE key = 'pending_wal_checkpoint'") == [("0",)]
    assert calls  # the committed flag alone triggered the checkpoint after the restart
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM tasks"))
        await session.execute(text("DELETE FROM retention_state"))
        await session.commit()


# --- P2-F2: purge script verification needs successful checkpoints -----------------------------------------------


def test_purge_script_verify_fails_on_a_busy_checkpoint(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("purge_script", REPO / "scripts" / "purge_task_prompts.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    db = tmp_path / "live.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript("CREATE TABLE tasks (id TEXT, prompt_text TEXT, prompt_expires_at TEXT, prompt_purged_at TEXT, "
                           "updated_at TEXT); CREATE TABLE task_evaluations (id TEXT, evaluator TEXT, note TEXT, "
                           "evaluated_at TEXT); CREATE TABLE retention_state (key TEXT PRIMARY KEY, value TEXT, "
                           "updated_at TEXT NOT NULL);")
    monkeypatch.setattr(script, "checkpoint_ok", lambda conn: False)
    assert script.main(["--db", str(db), "--all", "--include-backups", "--verify"]) == 1
    assert "checkpoint busy" in capsys.readouterr().err
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM retention_state").fetchone()[0] == 0


# --- JEV: P2-F1, P2-F6, P6-F1, P6-F2 ------------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = START

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def jev(client, monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "hermes")
    features.refresh()
    clock = Clock()
    monkeypatch.setattr(jev_scorer, "now", clock.now)
    monkeypatch.setattr(jev_scorer, "sleep", clock.sleep)
    yield clock
    features.refresh()


async def _task(ref, expires):
    await _exec("INSERT INTO tasks (id, project_name, session_id, turn_id, hierarchy_status, first_seen_at, "
                "last_seen_at, prompt_text, prompt_captured_at, prompt_expires_at, created_at, updated_at) VALUES "
                "(:id, 'hermes', 's', :id, 'root', :n, :n, 'Add tests.', :n, :e, :n, :n)",
                id=ref, n=_iso(START - timedelta(days=1)), e=_iso(expires))


async def _evaluate(client, refs):
    return await client.post("/api/tasks/evaluate", json={"task_refs": refs}, headers={"X-Ingest-Token": TOKEN})


async def test_prompt_expiring_during_a_cooldown_wait_is_never_sent(client, jev, monkeypatch):
    calls = []

    async def gateway(body, key):
        calls.append(body)
        return httpx.Response(200, json=OK_BODY)

    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task("a" * 32, START + timedelta(seconds=10))
    async with AsyncSessionLocal() as session:
        await jev_scorer.set_cooldown(session, "typesafe-ai", START + timedelta(seconds=30), "retry_after")
    run_id = (await _evaluate(client, ["a" * 32])).json()["run_id"]
    assert calls == []
    assert await _rows("SELECT status, error_type FROM task_evaluations WHERE run_id = :r", r=run_id) == [
        ("skipped", "prompt_expired")]


async def test_malformed_metadata_is_an_invalid_response_not_a_crashed_run(client, jev, monkeypatch):
    bad = copy.deepcopy(OK_BODY)
    bad["provider_metadata"] = [1]

    async def gateway(body, key):
        return httpx.Response(200, json=bad)

    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task("a" * 32, START + timedelta(days=5))
    run_id = (await _evaluate(client, ["a" * 32])).json()["run_id"]
    assert await _rows("SELECT status FROM evaluator_runs WHERE id = :r", r=run_id) == [("done",)]
    assert await _rows("SELECT status, error_type FROM task_evaluations") == [("error", "invalid_response")]
    assert await _rows("SELECT status FROM evaluator_attempts") == [("succeeded",)]


async def test_concurrent_evaluate_requests_reserve_exactly_one_run(client, jev, monkeypatch):
    dispatched = []

    async def slow_run(run_id, refs):
        dispatched.append(run_id)
        await asyncio.sleep(0.3)

    monkeypatch.setattr(jev_scorer, "run_evaluation", slow_run)
    await _task("a" * 32, START + timedelta(days=5))
    responses = await asyncio.gather(*(_evaluate(client, ["a" * 32]) for _ in range(2)))
    assert sorted(r.status_code for r in responses) == [202, 409]
    assert len(dispatched) == 1


async def test_monthly_ceiling_and_failed_attempts_count_at_reserved_cost(client, jev, monkeypatch):
    calls = []

    async def gateway(body, key):
        calls.append(body)
        return httpx.Response(200, json=OK_BODY)

    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _task("a" * 32, START + timedelta(days=5))
    await _exec("INSERT INTO evaluator_runs (id, evaluator, status, requested_count, queued_count, queued_at) "
                "VALUES ('old', 'jev', 'done', 1, 1, 'x')")
    # earlier this month (not today): 0.49999 of the 0.50 monthly budget, as a failed attempt at reserved cost
    await _exec("INSERT INTO evaluator_attempts (id, run_id, task_ref, provider, status, reserved_tokens, "
                "reserved_cost_usd, started_at) VALUES ('f', 'old', :t, 'typesafe-ai', 'failed', 1, 0.49999, :at)",
                t="a" * 32, at=_iso(START - timedelta(days=3)))
    run_id = (await _evaluate(client, ["a" * 32])).json()["run_id"]
    summary = (await client.get(f"/api/tasks/evaluator-runs/{run_id}")).json()
    assert (summary["status"], summary["stop_reason"]) == ("stopped", "budget_exceeded") and calls == []
