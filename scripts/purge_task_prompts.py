"""Purge task prompt text and labeller notes from the database and its backups.

Standalone: stdlib only, no imports from the application, so it also works during a rollback
(Plans/task-telemetry-jev-pilot/database.md, rollback runbook). Prints counts only, never text.

    python scripts/purge_task_prompts.py --db <path> (--expired | --all) [--include-backups] [--dry-run] [--verify]

--verify (with --all --include-backups) re-reads the DB and every backup after a WAL truncate and, only
if no prompt or note text remains anywhere, records retention_state.all_copy_purge_verified_at.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOT_A_DB = ("-wal", "-shm", "-journal")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _statements(mode: str, now: str) -> list[tuple[str, dict]]:
    if mode == "all":
        return [
            ("UPDATE tasks SET prompt_text = NULL, prompt_purged_at = COALESCE(prompt_purged_at, :now), "
             "updated_at = :now WHERE prompt_text IS NOT NULL", {"now": now}),
            ("UPDATE task_evaluations SET note = NULL WHERE note IS NOT NULL", {}),
        ]
    cutoff = _fmt(datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(days=30))
    return [
        ("UPDATE tasks SET prompt_text = NULL, prompt_purged_at = :now, updated_at = :now "
         "WHERE prompt_text IS NOT NULL AND prompt_expires_at <= :now", {"now": now}),
        ("UPDATE task_evaluations SET note = NULL WHERE evaluator = 'human' AND note IS NOT NULL "
         "AND evaluated_at <= :cutoff", {"cutoff": cutoff}),
    ]


def remaining_text(path: Path) -> int:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        if not _has(conn, "tasks"):
            return 0
        tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE prompt_text IS NOT NULL").fetchone()[0]
        notes = conn.execute("SELECT COUNT(*) FROM task_evaluations WHERE note IS NOT NULL").fetchone()[0]
        return tasks + notes


def purge_file(path: Path, mode: str, now: str, *, dry_run: bool, vacuum: bool) -> int:
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        if not _has(conn, "tasks"):
            return 0
        if dry_run:
            return remaining_text(path) if mode == "all" else conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE prompt_text IS NOT NULL AND prompt_expires_at <= ?", (now,)
            ).fetchone()[0]
        conn.execute("PRAGMA secure_delete=ON")
        changed = sum(conn.execute(sql, params).rowcount for sql, params in _statements(mode, now))
        conn.commit()
        checkpoint_ok(conn)
        if vacuum and (changed or conn.execute("PRAGMA freelist_count").fetchone()[0] > 0):
            conn.execute("VACUUM")
        return changed


def checkpoint_ok(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0


def backups(db: Path) -> list[Path]:
    return sorted(p for p in db.parent.glob(f"{db.name}.bak-v*") if p.is_file() and not p.name.endswith(NOT_A_DB))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--expired", action="store_const", dest="mode", const="expired")
    mode.add_argument("--all", action="store_const", dest="mode", const="all")
    parser.add_argument("--include-backups", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.verify and not (args.mode == "all" and args.include_backups):
        parser.error("--verify requires --all --include-backups")
    if not args.db.exists():
        parser.error(f"database not found: {args.db}")

    now = _fmt(_now())
    files = [args.db] + (backups(args.db) if args.include_backups else [])
    for path in files:
        count = purge_file(path, args.mode, now, dry_run=args.dry_run, vacuum=path != args.db)
        print(f"{'would purge' if args.dry_run else 'purged'} {count} row(s) in {path.name}")
    if args.verify and not args.dry_run:
        leftover = sum(remaining_text(path) for path in files)
        if leftover:
            print(f"VERIFY FAILED: {leftover} row(s) with text remain", file=sys.stderr)
            return 1
        busy = []
        for path in files:
            with closing(sqlite3.connect(path, timeout=10)) as conn:
                if not checkpoint_ok(conn):
                    busy.append(path.name)
        if busy:
            print(f"VERIFY FAILED: WAL checkpoint busy for {', '.join(busy)}; retry when readers are closed",
                  file=sys.stderr)
            return 1
        with closing(sqlite3.connect(args.db, timeout=10)) as conn:
            if _has(conn, "retention_state"):
                conn.execute(
                    "INSERT INTO retention_state (key, value, updated_at) VALUES "
                    "('all_copy_purge_verified_at', ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value, updated_at = excluded.updated_at",
                    (now, now),
                )
                conn.commit()
        print(f"verified: no prompt or note text in {len(files)} file(s); all_copy_purge_verified_at={now}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
