import os
import sqlite3

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from migrations import backup_database_if_needed, run_migrations
import models  # noqa: F401  # register SQLModel tables before create_all

DB_PATH = os.environ.get("DB_PATH", "token_inspector.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

_PRAGMAS = (
    "PRAGMA busy_timeout=5000",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA journal_mode=WAL",
    # Zero freed pages so purged prompt text does not linger in the main DB file.
    "PRAGMA secure_delete=ON",
)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _apply_pragmas(dbapi_connection) -> None:
    cursor = dbapi_connection.cursor()
    try:
        for pragma in _PRAGMAS:
            cursor.execute(pragma)
    finally:
        cursor.close()


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    _apply_pragmas(dbapi_connection)


def _migration_engine(db_path: str) -> AsyncEngine:
    """Engine whose transactions really wrap DDL.

    The sqlite3 driver does not emit BEGIN before DDL, so create_all and each ALTER would
    autocommit. With the driver's own transaction handling off and an explicit
    BEGIN IMMEDIATE, create_all and every migration commit or roll back together.
    """
    migration_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    @event.listens_for(migration_engine.sync_engine, "connect")
    def _connect(dbapi_connection, _connection_record) -> None:
        dbapi_connection.isolation_level = None
        _apply_pragmas(dbapi_connection)

    @event.listens_for(migration_engine.sync_engine, "begin")
    def _begin(conn) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return migration_engine


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db(db_path: str | None = None) -> None:
    path = db_path or DB_PATH
    if path != ":memory:":
        backup_database_if_needed(path)
    if sqlite3.sqlite_version_info < (3, 24, 0):
        raise RuntimeError(
            f"SQLite 3.24+ is required for atomic idempotent ingest; found {sqlite3.sqlite_version}"
        )
    migration_engine = _migration_engine(path)
    try:
        async with migration_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            await run_migrations(conn)
            mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            if path != ":memory:" and str(mode).lower() != "wal":
                raise RuntimeError(f"SQLite WAL mode was not enabled (journal_mode={mode!r})")
    finally:
        await migration_engine.dispose()
