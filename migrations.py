from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

import task_store

LATEST_SCHEMA_VERSION = 10
Migration = Callable[[AsyncConnection], Awaitable[None]]


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _count_and_version(conn: sqlite3.Connection) -> tuple[int | None, int]:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    count = conn.execute("SELECT COUNT(*) FROM token_events").fetchone()[0] if "token_events" in tables else None
    version = 0
    if "schema_migrations" in tables:
        version = int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0] or 0)
    return count, version


def verify_backup(source: Path, target: Path, current: int) -> None:
    """Fail closed unless the backup is intact and matches the source (runs before any mutation)."""
    with closing(_read_only(source)) as src:
        source_count, _ = _count_and_version(src)
    with closing(_read_only(target)) as bak:
        integrity = bak.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"pre-migration backup verification failed: integrity_check={integrity!r}")
        backup_count, backup_version = _count_and_version(bak)
    if backup_count != source_count:
        raise RuntimeError(
            f"pre-migration backup verification failed: token_events {backup_count} != {source_count}"
        )
    if backup_version != current:
        raise RuntimeError(
            f"pre-migration backup verification failed: schema version {backup_version} != {current}"
        )


def backup_database_if_needed(db_path: str) -> Path | None:
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0 or str(path) == ":memory:":
        return None
    current = 0
    try:
        with sqlite3.connect(path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if exists:
                row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
                current = int(row[0] or 0)
    except sqlite3.Error:
        current = 0
    if current >= LATEST_SCHEMA_VERSION:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.bak-v{current}-{stamp}")
    # The online-backup API includes pages still in the WAL file; a file copy would miss them.
    with closing(sqlite3.connect(path)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    verify_backup(path, target, current)
    return target


async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    row = (
        await conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table},
        )
    ).first()
    return row is not None


async def _columns(conn: AsyncConnection, table: str) -> dict[str, dict]:
    if not await _table_exists(conn, table):
        return {}
    rows = (await conn.execute(text(f'PRAGMA table_info("{table}")'))).mappings().all()
    return {str(row["name"]): dict(row) for row in rows}


async def _indexes(conn: AsyncConnection, table: str) -> set[str]:
    if not await _table_exists(conn, table):
        return set()
    rows = (await conn.execute(text(f'PRAGMA index_list("{table}")'))).mappings().all()
    return {str(row["name"]) for row in rows}


async def _add_columns(conn: AsyncConnection, table: str, definitions: dict[str, str]) -> None:
    existing = await _columns(conn, table)
    for name, definition in definitions.items():
        if name not in existing:
            await conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}'))


async def _m1(conn: AsyncConnection) -> None:
    return None


async def _m2(conn: AsyncConnection) -> None:
    await _add_columns(conn, "token_events", {"complexity": "INTEGER", "prompt_text": "TEXT"})


async def _m3(conn: AsyncConnection) -> None:
    await _add_columns(
        conn,
        "token_events",
        {
            "provider": "TEXT",
            "tool_name": "TEXT",
            "session_id": "TEXT",
            "trace_id": "TEXT",
            "span_id": "TEXT",
            "parent_span_id": "TEXT",
            "turn_id": "TEXT",
            "api_request_id": "TEXT",
            "client_event_id": "TEXT",
            "tool_call_count": "INTEGER",
        },
    )


async def _m4(conn: AsyncConnection) -> None:
    await _add_columns(
        conn,
        "token_events",
        {
            "occurred_at": "TEXT",
            "event_type": "TEXT NOT NULL DEFAULT 'llm_request'",
            "model_requested": "TEXT",
            "pricing_model": "TEXT",
            "task_id": "TEXT",
            "tool_call_id": "TEXT",
            "turn_index": "INTEGER",
            "environment": "TEXT",
            "platform": "TEXT",
            "user_id_hash": "TEXT",
            "tags_json": "TEXT",
            "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
            "total_tokens": "INTEGER",
            "http_status": "INTEGER",
            "finish_reason": "TEXT",
            "error_type": "TEXT",
            "attempt": "INTEGER NOT NULL DEFAULT 1",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "ttft_ms": "INTEGER",
            "prompt_hash": "TEXT",
            "prompt_length": "INTEGER",
            "cost_status": "TEXT NOT NULL DEFAULT 'unpriced'",
            "cost_status_reason": "TEXT",
            "pricing_version": "INTEGER",
        },
    )


async def _m5(conn: AsyncConnection) -> None:
    indexes = await _indexes(conn, "token_events")
    statements = {
        "ix_token_events_session_id": "CREATE INDEX ix_token_events_session_id ON token_events(session_id)",
        "ix_token_events_trace_id": "CREATE INDEX ix_token_events_trace_id ON token_events(trace_id)",
        "idx_token_events_project_recorded": "CREATE INDEX idx_token_events_project_recorded ON token_events(project_name, recorded_at)",
    }
    for name, statement in statements.items():
        if name not in indexes:
            await conn.execute(text(statement))
    for obsolete in (
        "ix_token_events_provider",
        "ix_token_events_tool_name",
        "ix_token_events_span_id",
        "ix_token_events_parent_span_id",
        "ix_token_events_turn_id",
        "ix_token_events_api_request_id",
        "ix_token_events_client_event_id",
    ):
        await conn.execute(text(f'DROP INDEX IF EXISTS "{obsolete}"'))


async def _m6(conn: AsyncConnection) -> None:
    duplicate_rows = (
        await conn.execute(
            text(
                "SELECT project_name, client_event_id, COUNT(*) AS n "
                "FROM token_events WHERE client_event_id IS NOT NULL "
                "GROUP BY project_name, client_event_id HAVING COUNT(*) > 1 LIMIT 20"
            )
        )
    ).mappings().all()
    if duplicate_rows:
        raise RuntimeError(f"duplicate client_event_id rows block migration: {list(duplicate_rows)!r}")
    await conn.execute(text("DROP INDEX IF EXISTS idx_token_events_client_event_id"))
    indexes = await _indexes(conn, "token_events")
    if "idx_token_events_project_client_event" not in indexes:
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX idx_token_events_project_client_event "
                "ON token_events(project_name, client_event_id) "
                "WHERE client_event_id IS NOT NULL"
            )
        )


async def _m7(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "UPDATE token_events SET "
            "cost_status = CASE WHEN estimated_cost_usd IS NULL THEN 'unpriced' ELSE 'legacy' END, "
            "occurred_at = COALESCE(occurred_at, recorded_at), "
            "pricing_model = COALESCE(pricing_model, model) "
            "WHERE cost_status IS NULL OR cost_status = 'unpriced'"
        )
    )


async def _m8(conn: AsyncConnection) -> None:
    await _add_columns(
        conn,
        "pricing_rules",
        {
            "cache_read_price_per_1m": "REAL",
            "cache_creation_price_per_1m": "REAL",
            "pricing_version": "INTEGER NOT NULL DEFAULT 1",
        },
    )


async def _m9(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS model_aliases ("
            "alias TEXT PRIMARY KEY NOT NULL, "
            "model TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_model_aliases_model ON model_aliases(model)"))


# Column definitions match what SQLModel.create_all emits for TokenEvent, in declaration order,
# so fresh and migrated schemas agree.
_M10_TOKEN_EVENT_COLUMNS = {
    "complexity_method": "VARCHAR(32)",
    "request_system_chars": "INTEGER",
    "request_history_chars": "INTEGER",
    "request_tool_output_chars": "INTEGER",
    "request_file_content_chars": "INTEGER",
    "request_file_ref_count": "INTEGER",
    "request_tool_names_json": "VARCHAR",
}


async def _m10(conn: AsyncConnection) -> None:
    import models

    def _create_tables(sync_conn) -> None:
        # The model classes define the DDL; create_all normally created these already.
        for model in (
            models.Task,
            models.EvaluatorRun,
            models.TaskEvaluation,
            models.EvaluatorAttempt,
            models.ProviderCooldown,
            models.RetentionState,
        ):
            model.__table__.create(sync_conn, checkfirst=True)
            for index in model.__table__.indexes:
                index.create(sync_conn, checkfirst=True)

    await conn.run_sync(_create_tables)
    await _add_columns(conn, "token_events", _M10_TOKEN_EVENT_COLUMNS)
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_token_events_project_session_turn "
            "ON token_events (project_name, session_id, turn_id)"
        )
    )
    await task_store.backfill_tasks(conn, models._now())


MIGRATIONS: list[tuple[int, Migration]] = [
    (1, _m1),
    (2, _m2),
    (3, _m3),
    (4, _m4),
    (5, _m5),
    (6, _m6),
    (7, _m7),
    (8, _m8),
    (9, _m9),
    (10, _m10),
]


async def run_migrations(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
    )
    applied = {
        int(row[0])
        for row in (await conn.execute(text("SELECT version FROM schema_migrations"))).all()
    }
    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        await migration(conn)
        applied_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        await conn.execute(
            text("INSERT INTO schema_migrations(version, applied_at) VALUES (:version, :applied_at)"),
            {"version": version, "applied_at": applied_at},
        )
