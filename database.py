import os
import sqlite3

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from migrations import backup_database_if_needed, run_migrations
import models  # noqa: F401  # register SQLModel tables before create_all

DB_PATH = os.environ.get("DB_PATH", "token_inspector.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    if DB_PATH != ":memory:":
        backup_database_if_needed(DB_PATH)
    if sqlite3.sqlite_version_info < (3, 24, 0):
        raise RuntimeError(
            f"SQLite 3.24+ is required for atomic idempotent ingest; found {sqlite3.sqlite_version}"
        )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await run_migrations(conn)
        mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
        if DB_PATH != ":memory:" and str(mode).lower() != "wal":
            raise RuntimeError(f"SQLite WAL mode was not enabled (journal_mode={mode!r})")
