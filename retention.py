"""Retention of task prompts and labeller notes (backend.md §5; r2 items 1-2).

The purge NULLs columns (no row is deleted), commits, then truncates the WAL whenever a
checkpoint is pending, and re-purges + VACUUMs DB-directory backups. Bookkeeping lives in
`retention_state` so it survives restarts. Nothing here ever logs prompt or note text.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text

import features

log = logging.getLogger(__name__)
NOTE_RETENTION = timedelta(days=30)
_NOT_A_DB_SUFFIXES = ("-wal", "-shm", "-journal")


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _purge_statements(now: str, note_cutoff: str) -> list[tuple[str, dict]]:
    return [
        (
            "UPDATE tasks SET prompt_text = NULL, prompt_purged_at = :now, updated_at = :now "
            "WHERE prompt_text IS NOT NULL AND prompt_expires_at <= :now",
            {"now": now},
        ),
        (
            "UPDATE task_evaluations SET note = NULL WHERE evaluator = 'human' AND note IS NOT NULL "
            "AND evaluated_at <= :cutoff",
            {"cutoff": note_cutoff},
        ),
    ]


def backup_files(db_path: str) -> list[Path]:
    path = Path(db_path)
    if db_path == ":memory:" or not path.parent.exists():
        return []
    return sorted(
        p for p in path.parent.glob(f"{path.name}.bak-v*")
        if p.is_file() and not p.name.endswith(_NOT_A_DB_SUFFIXES)
    )


def _has_tasks_table(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone() is not None


def _repurge_backup(path: Path, now: str, note_cutoff: str) -> bool:
    """Same UPDATEs on a backup, then physical cleanup that is retried until it succeeds.

    Cleanup is stateless: any free page (a purge that was not VACUUMed yet) triggers VACUUM again,
    and a busy checkpoint raises so the run is reported as failed and retried.
    """
    with closing(sqlite3.connect(path)) as conn:
        if not _has_tasks_table(conn):
            return False
        conn.execute("PRAGMA secure_delete=ON")
        changed = 0
        for sql, params in _purge_statements(now, note_cutoff):
            changed += conn.execute(sql, params).rowcount
        conn.commit()
        if conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
            raise BackupCleanupError("backup checkpoint busy")
        if changed or conn.execute("PRAGMA freelist_count").fetchone()[0] > 0:
            conn.execute("VACUUM")
        return bool(changed)


class BackupCleanupError(RuntimeError):
    pass


def backup_text_present(path: Path) -> bool:
    """True when a backup holds text; an unreadable backup counts as retained (conservative)."""
    try:
        return _backup_text_present(path)
    except sqlite3.Error:
        return True


def _backup_text_present(path: Path) -> bool:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        if not _has_tasks_table(conn):
            return False
        tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE prompt_text IS NOT NULL").fetchone()[0]
        notes = conn.execute("SELECT COUNT(*) FROM task_evaluations WHERE note IS NOT NULL").fetchone()[0]
        return bool(tasks or notes)


def wal_checkpoint(db_path: str) -> bool:
    """TRUNCATE checkpoint on a fresh connection outside any transaction; True when not busy."""
    if db_path == ":memory:":
        return True
    with closing(sqlite3.connect(db_path, timeout=5)) as conn:
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    return busy == 0


async def get_state(session, key: str) -> Optional[str]:
    row = (await session.execute(text("SELECT value FROM retention_state WHERE key = :k"), {"k": key})).first()
    return row[0] if row else None


async def set_state(session, key: str, value: Optional[str], now: str) -> None:
    await session.execute(
        text(
            "INSERT INTO retention_state (key, value, updated_at) VALUES (:k, :v, :now) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
        ),
        {"k": key, "v": value, "now": now},
    )


async def purge_expired(session, db_path: str, *, dry_run: bool = False, now_dt: Optional[datetime] = None) -> dict:
    now_dt = now_dt or datetime.now(timezone.utc)
    now, note_cutoff = _fmt(now_dt), _fmt(now_dt - NOTE_RETENTION)
    eligible = (
        await session.execute(
            text("SELECT COUNT(*) FROM tasks WHERE prompt_text IS NOT NULL AND prompt_expires_at <= :now"),
            {"now": now},
        )
    ).scalar_one()
    notes = (
        await session.execute(
            text("SELECT COUNT(*) FROM task_evaluations WHERE evaluator = 'human' AND note IS NOT NULL "
                 "AND evaluated_at <= :cutoff"),
            {"cutoff": note_cutoff},
        )
    ).scalar_one()
    backups = backup_files(db_path)
    result = {"dry_run": dry_run, "cutoff": now, "eligible": eligible, "purged": 0, "notes_purged": 0,
              "backups_scanned": len(backups), "backups_repurged": 0, "checkpoint_ok": None}
    if dry_run:
        return result
    try:
        changed = []
        for sql, params in _purge_statements(now, note_cutoff):
            changed.append((await session.execute(text(sql), params)).rowcount or 0)
        result["purged"], result["notes_purged"] = changed
        if sum(changed):
            await set_state(session, "pending_wal_checkpoint", "1", now)
        await session.commit()  # r2 item 1: deletion is committed before any checkpoint

        errors = []
        if await get_state(session, "pending_wal_checkpoint") == "1":
            ok = await asyncio.to_thread(wal_checkpoint, db_path)
            result["checkpoint_ok"] = ok
            await set_state(session, "last_checkpoint_ok", "true" if ok else "false", now)
            if ok:
                await set_state(session, "pending_wal_checkpoint", "0", now)
            else:
                errors.append("checkpoint_busy")  # physical erasure is incomplete: not a successful purge
        repurged = 0
        for backup in backups:
            try:
                if await asyncio.to_thread(_repurge_backup, backup, now, note_cutoff):
                    repurged += 1
            except (sqlite3.Error, BackupCleanupError) as exc:
                errors.append(f"backup_{type(exc).__name__}")  # one bad backup never blocks the others
        result["backups_repurged"] = repurged
        result["errors"] = errors
        if errors:
            log.error("[RETENTION] purge incomplete: %s", ",".join(errors))
        for key, value in (
            ("last_purge_at", now), ("last_purge_ok", "false" if errors else "true"),
            ("last_error", ",".join(errors) or None),
            ("last_backups_scanned", str(len(backups))), ("last_backups_repurged", str(repurged)),
        ):
            await set_state(session, key, value, now)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        await set_state(session, "last_purge_at", now, now)
        await set_state(session, "last_purge_ok", "false", now)
        await set_state(session, "last_error", type(exc).__name__, now)
        await session.commit()
        log.error("[RETENTION] purge failed: %s", type(exc).__name__)
        raise
    log.info(
        "[RETENTION] purged=%d notes=%d backups=%d/%d checkpoint=%s",
        result["purged"], result["notes_purged"], len(backups), result["backups_repurged"],
        result["checkpoint_ok"],
    )
    return result


async def retained_data_present(session, db_path: str) -> bool:
    live = (
        await session.execute(
            text("SELECT (SELECT COUNT(*) FROM tasks WHERE prompt_text IS NOT NULL) + "
                 "(SELECT COUNT(*) FROM task_evaluations WHERE note IS NOT NULL)")
        )
    ).scalar_one()
    if live or await get_state(session, "pending_wal_checkpoint") == "1":
        return True
    return any([await asyncio.to_thread(backup_text_present, b) for b in backup_files(db_path)])


@dataclass(frozen=True)
class Schedule:
    scheduled: bool
    interval_s: int
    interval_refused: bool


def decide_schedule(state: features.Features, retained: bool, verified_at: Optional[str]) -> Schedule:
    """r2 item 2: the loop runs whenever capture is on or data is retained; 0 needs a verified all-copy purge."""
    raw = state.purge_interval_raw
    interval, _ = features.purge_interval({"TASK_PROMPT_PURGE_INTERVAL_S": raw} if raw is not None else {})
    if interval is None:
        return Schedule(True if (state.capture_enabled or retained) else False,
                        features.PURGE_INTERVAL_DEFAULT_S, True)
    if interval == 0:
        if not retained and not state.capture_enabled and verified_at:
            return Schedule(False, 0, False)
        return Schedule(True, features.PURGE_INTERVAL_DEFAULT_S, True)
    return Schedule(state.capture_enabled or retained, interval, False)


async def status(session, db_path: str) -> dict:
    state = features.current()
    now = _fmt(datetime.now(timezone.utc))
    retained = await retained_data_present(session, db_path)
    verified = await get_state(session, "all_copy_purge_verified_at")
    schedule = decide_schedule(state, retained, verified)
    pending = (
        await session.execute(
            text("SELECT COUNT(*), MIN(prompt_expires_at) FROM tasks WHERE prompt_text IS NOT NULL "
                 "AND prompt_expires_at <= :now"),
            {"now": now},
        )
    ).one()

    def flag(value: Optional[str]) -> Optional[bool]:
        return None if value is None else value == "true"

    return {
        "capture_enabled": state.capture_enabled,
        "disabled_reason": state.capture_disabled_reason,
        "purge_interval_s": schedule.interval_s,
        "last_purge_at": await get_state(session, "last_purge_at"),
        "last_purge_ok": flag(await get_state(session, "last_purge_ok")),
        "last_error": await get_state(session, "last_error"),
        "pending_expired": int(pending[0]),
        "last_checkpoint_ok": flag(await get_state(session, "last_checkpoint_ok")),
        "pending_wal_checkpoint": await get_state(session, "pending_wal_checkpoint") == "1",
        "backups_scanned": int(await get_state(session, "last_backups_scanned") or 0),
        "backups_repurged": int(await get_state(session, "last_backups_repurged") or 0),
        "scheduled": schedule.scheduled,
        "interval_refused": schedule.interval_refused,
        "retained_data_present": retained,
        "oldest_overdue_expires_at": pending[1],
        "all_copy_purge_verified_at": verified,
    }


async def _schedule(session_factory, db_path: str) -> Schedule:
    async with session_factory() as session:
        retained = await retained_data_present(session, db_path)
        verified = await get_state(session, "all_copy_purge_verified_at")
    return decide_schedule(features.current(), retained, verified)


async def retention_loop(session_factory, db_path: str) -> None:
    """Background loop after the startup purge: wait one interval, then purge if data is retained.

    The decision is re-made every cycle, so the loop picks up data that appears later.
    """
    while True:
        try:
            schedule = await _schedule(session_factory, db_path)
            if schedule.interval_refused:
                log.error("[RETENTION] purge interval refused; using %ss", schedule.interval_s)
            await asyncio.sleep(schedule.interval_s or features.PURGE_INTERVAL_DEFAULT_S)
            if (await _schedule(session_factory, db_path)).scheduled:
                async with session_factory() as session:
                    await purge_expired(session, db_path)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(60)
