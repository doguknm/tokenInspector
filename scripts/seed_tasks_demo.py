"""Seed a fixed demo database for the Tasks view (dev / E2E only; backend.md §9).

    python scripts/seed_tasks_demo.py --db /tmp/demo.db [--variant standard|many-scored]

Refuses if --db exists. Times are relative to now. No JEV call is made. Tasks are Hermes turns
(D0 Drift Log), so each demo "task id" below is the turn_id.
Counts (standard): 7 or 30 days -> 65 tasks; 90 days -> 66; root-only at 30 days -> 64; scored -> 2;
demo-alpha -> 4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CANARY_PROMPT = "CANARY-PROMPT-TEXT-7731"
CANARY_NOTE = "CANARY-NOTE-5512"
HOSTILE_TURN = 'x"><img src=x onerror="window.__xss=1">'
HOSTILE_SESSION = '<svg onload="window.__xss=2">'
LEGEND = [
    "Single obvious routine step",
    "A few known steps",
    "Multi-file / multi-step analysis with tests",
    "Unclear root cause or multi-system coordination",
    "Open-ended research or deep architectural uncertainty",
]


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Seeder:
    def __init__(self, conn: sqlite3.Connection, now: datetime) -> None:
        self.conn, self.now = conn, now

    def ago(self, days: float, minutes: float = 0) -> str:
        return _fmt(self.now - timedelta(days=days, minutes=minutes))

    def task(self, project, session, turn, days, *, rs1=None, hierarchy="root", parent=None, prompt=None,
             prompt_state="none", completion=None, completed_at=None, last_seen=None) -> str:
        from task_store import task_ref

        ref = task_ref(project, session, turn)
        first = self.ago(days, 5)
        values = {
            "id": ref, "project_name": project, "session_id": session, "turn_id": turn, "source_task_id": turn,
            "parent_task_ref": parent, "root_task_ref": parent, "hierarchy_status": hierarchy, "source": "ingest",
            "first_seen_at": first, "last_seen_at": last_seen or self.ago(days), "completion": completion,
            "completed_at": completed_at, "start_complexity": rs1,
            "start_complexity_method": "request-shape-v1" if rs1 else None,
            "start_complexity_event_at": first if rs1 else None, "start_complexity_event_id": None,
            "prompt_text": None, "prompt_hash": None, "prompt_length": None, "prompt_truncated": 0,
            "prompt_redaction_version": None, "prompt_captured_at": None, "prompt_expires_at": None,
            "prompt_purged_at": None, "created_at": first, "updated_at": first,
        }
        if prompt_state in ("retained", "expired", "purged"):
            captured = self.now - timedelta(days=days) if prompt_state != "expired" else self.now - timedelta(days=31)
            values.update({"prompt_captured_at": _fmt(captured), "prompt_expires_at": _fmt(captured + timedelta(days=30)),
                           "prompt_redaction_version": "task-redact-v1", "prompt_length": len(prompt or "x")})
            if prompt_state != "purged":
                values["prompt_text"] = prompt or f"Demo request for {turn}."
            else:
                values["prompt_purged_at"] = self.ago(0)
        columns = ", ".join(values)
        self.conn.execute(f"INSERT INTO tasks ({columns}) VALUES ({', '.join('?' * len(values))})",
                          list(values.values()))
        return ref

    def event(self, project, session, turn, days, *, event_type="llm_request", priced=True, minutes=0, **extra):
        cost = 0.00042 if priced and event_type == "llm_request" else None
        row = {
            "id": str(uuid.uuid4()), "project_name": project, "recorded_at": self.ago(days, minutes),
            "occurred_at": self.ago(days, minutes), "event_type": event_type,
            "model": "claude-sonnet-4-6" if priced else "demo-unpriced-model", "session_id": session,
            "task_id": turn, "turn_id": turn, "prompt_tokens": 100 if event_type == "llm_request" else 0,
            "completion_tokens": 20 if event_type == "llm_request" else 0, "cache_read_tokens": 0,
            "cache_creation_tokens": 0, "reasoning_tokens": 0, "status": "success", "attempt": 1, "retry_count": 0,
            "estimated_cost_usd": cost,
            "cost_status": ("priced" if cost is not None else "unpriced") if event_type == "llm_request" else "unpriced",
        }
        row.update(extra)
        self.conn.execute(f"INSERT INTO token_events ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                          list(row.values()))

    def jev(self, ref, status="ok", raw=None, confidence=None, probabilities=None, provider="typesafe-ai", days=0.5):
        self.conn.execute(
            "INSERT INTO task_evaluations (id, task_ref, evaluator, rubric_version, status, raw_score, confidence, "
            "probabilities_json, legend_json, model, provider_used, input_hash, input_tokens, cost_usd, http_attempts, "
            "error_type, evaluated_at) VALUES (?, ?, 'jev', 'difficulty-v0', ?, ?, ?, ?, ?, 'typesafe-ai/jev', ?, ?, "
            "446, 0.000018732, 1, ?, ?)",
            (str(uuid.uuid4()), ref, status, raw, confidence,
             json.dumps(probabilities) if probabilities else None, json.dumps(LEGEND) if status == "ok" else None,
             provider, uuid.uuid4().hex, None if status == "ok" else "http_5xx", self.ago(days)),
        )

    def human(self, ref, label, note):
        self.conn.execute(
            "INSERT INTO task_evaluations (id, task_ref, evaluator, rubric_version, status, label, labeler, note, "
            "http_attempts, evaluated_at) VALUES (?, ?, 'human', 'difficulty-v0', 'ok', ?, 'demo', ?, 0, ?)",
            (str(uuid.uuid4()), ref, label, note, self.ago(0.2)),
        )


def seed(conn: sqlite3.Connection, variant: str, now: datetime) -> None:
    s = Seeder(conn, now)
    # demo-alpha: task-2 then task-1 in one session -> task-2 next_task, task-1 session_end
    t2 = s.task("demo-alpha", "alpha-s1", "demo-task-2", 2, rs1=4, prompt_state="retained",
                completion="next_task", completed_at=s.ago(1, 5))
    s.event("demo-alpha", "alpha-s1", "demo-task-2", 2)
    s.event("demo-alpha", "alpha-s1", "demo-task-2", 2, priced=False, minutes=1)
    s.jev(t2, raw=3.6, confidence=0.61, probabilities=[0, 0, 0.1, 0.2, 0.7], provider="digitalocean")
    t1 = s.task("demo-alpha", "alpha-s1", "demo-task-1", 1, rs1=2, prompt=f"Refactor the parser. {CANARY_PROMPT}",
                prompt_state="retained", completion="session_end", completed_at=s.ago(0, 30))
    s.event("demo-alpha", "alpha-s1", "demo-task-1", 1)
    for i in range(3):
        s.event("demo-alpha", "alpha-s1", "demo-task-1", 1, event_type="tool_call", minutes=i + 1, tool_name="read_file")
    s.event("demo-alpha", "alpha-s1", "gw", 0, event_type="session", minutes=30, finish_reason="end", turn_id="gw")
    s.jev(t1, raw=2.2, confidence=0.83, probabilities=[0, 0, 0.8, 0.2, 0])
    s.human(t1, 2, f"looks moderate {CANARY_NOTE}")
    s.task("demo-alpha", "alpha-child", "demo-task-1-child", 1, rs1=1, hierarchy="child", parent=t1)
    s.event("demo-alpha", "alpha-child", "demo-task-1-child", 1)
    s.task("demo-alpha", "alpha-s3", "demo-task-3", 3, prompt_state="purged")
    s.event("demo-alpha", "alpha-s3", "demo-task-3", 3, priced=False)
    s.event("demo-alpha", "alpha-s3", "demo-task-3", 3, priced=False, minutes=1)
    # demo-beta
    s.task("demo-beta", "beta-s4", "demo-task-4", 2, rs1=5, prompt_state="retained")
    s.event("demo-beta", "beta-s4", "demo-task-4", 2)
    hostile = s.task("demo-beta", HOSTILE_SESSION, HOSTILE_TURN, 1, rs1=3)
    s.event("demo-beta", HOSTILE_SESSION, HOSTILE_TURN, 1)
    s.jev(hostile, status="error", provider="typesafe-ai")
    shared_last_seen = s.ago(1.5)
    for i in range(1, 60):
        turn = f"demo-filler-{i:02d}"
        days = 1 + (i % 5)
        s.task("demo-beta", f"beta-f{i:02d}", turn, days, rs1=1 + (i % 5),
               prompt_state="expired" if i == 59 else "none",
               last_seen=shared_last_seen if i <= 10 else None)
        s.event("demo-beta", f"beta-f{i:02d}", turn, days)
    s.task("demo-beta", "beta-old", "demo-old", 40, rs1=3, hierarchy="unknown")
    s.event("demo-beta", "beta-old", "demo-old", 40)
    if variant == "many-scored":
        for i in range(205):
            ref = s.task("demo-gamma", f"gamma-{i:03d}", f"demo-gamma-{i:03d}", 1 + (i % 5), rs1=1 + (i % 5))
            s.event("demo-gamma", f"gamma-{i:03d}", f"demo-gamma-{i:03d}", 1 + (i % 5))
            raw = (i % 41) / 10
            s.jev(ref, raw=raw, confidence=0.7, probabilities=[0.2, 0.2, 0.2, 0.2, 0.2])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--variant", choices=("standard", "many-scored"), default="standard")
    args = parser.parse_args(argv)
    if args.db.exists():
        print(f"refusing: {args.db} already exists", file=sys.stderr)
        return 1
    os.environ["DB_PATH"] = str(args.db)
    import database

    asyncio.run(database.init_db(str(args.db)))
    with closing(sqlite3.connect(args.db)) as conn:
        seed(conn, args.variant, datetime.now(timezone.utc))
        conn.commit()
    print(f"seeded {args.variant} demo database at {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
