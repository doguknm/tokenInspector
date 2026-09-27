"""Demo seed counts and shapes (tests-e2e depends on them)."""

import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed_tasks_demo.py"


def _seed(tmp_path, variant="standard"):
    db = tmp_path / f"{variant}.db"
    run = subprocess.run([sys.executable, str(SCRIPT), "--db", str(db), "--variant", variant],
                         capture_output=True, text=True, cwd=tmp_path)
    assert run.returncode == 0, run.stderr
    return db


def _count(db, sql):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql).fetchone()[0]


def test_standard_counts(tmp_path):
    db = _seed(tmp_path)
    days = lambda n: f"SELECT COUNT(*) FROM tasks WHERE last_seen_at >= strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-{n} days')"
    assert _count(db, days(7)) == 65
    assert _count(db, days(30)) == 65
    assert _count(db, days(90)) == 66
    assert _count(db, days(30) + " AND hierarchy_status = 'root'") == 64
    assert _count(db, "SELECT COUNT(DISTINCT task_ref) FROM task_evaluations WHERE evaluator='jev' AND status='ok'") == 2
    assert _count(db, "SELECT COUNT(*) FROM tasks WHERE project_name = 'demo-alpha'") == 4
    assert _count(db, "SELECT COUNT(*) FROM tasks WHERE turn_id LIKE 'x\"><img%'") == 1
    assert _count(db, "SELECT COUNT(DISTINCT last_seen_at) FROM tasks WHERE turn_id BETWEEN 'demo-filler-01' "
                      "AND 'demo-filler-10'") == 1


def test_many_scored_and_refuses_existing_db(tmp_path):
    db = _seed(tmp_path, "many-scored")
    assert _count(db, "SELECT COUNT(DISTINCT task_ref) FROM task_evaluations WHERE evaluator='jev' AND status='ok'") == 207
    again = subprocess.run([sys.executable, str(SCRIPT), "--db", str(db)], capture_output=True, text=True)
    assert again.returncode == 1 and "refusing" in again.stderr
