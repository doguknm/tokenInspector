import os
import tempfile
from pathlib import Path

import httpx
import pytest_asyncio
from sqlalchemy import delete

_TEST_DIR = Path(tempfile.mkdtemp(prefix="token-inspector-tests-"))
os.environ["DB_PATH"] = str(_TEST_DIR / "test.db")
os.environ.pop("INGEST_TOKEN", None)
os.environ["STORE_RAW_PROMPTS"] = "0"

from database import AsyncSessionLocal  # noqa: E402
from main import app, lifespan  # noqa: E402
from models import ModelAlias, TokenEvent  # noqa: E402


@pytest_asyncio.fixture
async def client():
    async with lifespan(app):
        async with AsyncSessionLocal() as session:
            await session.execute(delete(TokenEvent))
            await session.execute(delete(ModelAlias))
            await session.commit()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
            yield value
