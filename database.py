from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import sessionmaker
import os

DB_PATH = os.environ.get("DB_PATH", "token_inspector.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        for col_sql in [
            "ALTER TABLE token_events ADD COLUMN complexity INTEGER",
            "ALTER TABLE token_events ADD COLUMN prompt_text TEXT",
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception:
                pass
