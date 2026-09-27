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
os.environ["TOKEN_INSPECTOR_ALLOWED_HOSTS"] = "127.0.0.1,localhost,test"
for _name in ("STORE_TASK_PROMPTS", "JEV_ENABLED", "AI_GATEWAY_API_KEY", "TASK_PROMPT_PURGE_INTERVAL_S"):
    os.environ.pop(_name, None)

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
