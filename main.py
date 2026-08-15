import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from database import AsyncSessionLocal, DB_PATH, init_db
from models import PricingRule, SEED_PRICING, _now
from routes import analytics, events, settings

log = logging.getLogger(__name__)


async def _seed_pricing() -> None:
    async with AsyncSessionLocal() as session:
        existing = set((await session.exec(select(PricingRule.model))).all())
        for item in SEED_PRICING:
            if item["model"] in existing:
                continue
            session.add(PricingRule(**item, updated_at=_now()))
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_pricing()
    raw_enabled = os.environ.get("STORE_RAW_PROMPTS", "").lower() in {"1", "true", "yes", "on"}
    log.info("Token Inspector ready: db=%s raw_prompts=%s", DB_PATH, raw_enabled)
    yield


app = FastAPI(title="Token Inspector", lifespan=lifespan)
app.include_router(events.router)
app.include_router(analytics.router)
app.include_router(settings.router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")
