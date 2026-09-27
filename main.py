import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

import features
import jev_scorer
import retention
from database import AsyncSessionLocal, DB_PATH, init_db
from models import PricingRule, SEED_PRICING, _now
from routes import analytics, events, meta, settings, tasks

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
    _log_feature_state(features.refresh())
    async with AsyncSessionLocal() as session:
        await jev_scorer.recover_after_restart(session)
    await _startup_purge()
    loop_task = asyncio.create_task(retention.retention_loop(AsyncSessionLocal, DB_PATH))
    log.info("Token Inspector ready: db=%s raw_prompts=%s", DB_PATH, raw_enabled)
    try:
        yield
    finally:
        loop_task.cancel()
        with suppress(asyncio.CancelledError):
            await loop_task


async def _startup_purge() -> None:
    """Runs before serving, whatever the flags, so a restored backup is purged immediately."""
    try:
        async with AsyncSessionLocal() as session:
            await retention.purge_expired(session, DB_PATH)
    except Exception:
        log.error("[RETENTION] startup purge failed; read-time expiry still applies")


def _log_feature_state(state: features.Features) -> None:
    for name, reason in (
        ("task prompt capture", state.capture_disabled_reason),
        ("jev worker", state.jev_disabled_reason),
    ):
        if reason is None:
            log.info("[SECURITY] %s enabled", name)
        elif reason in {"not_enabled", "credentials_missing"}:
            log.info("[SECURITY] %s disabled: %s", name, reason)
        else:
            log.error("[SECURITY] %s disabled: %s", name, reason)


app = FastAPI(title="Token Inspector", lifespan=lifespan)
# Host allowlist blocks DNS rebinding against the loopback service.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=features.host_list())
app.include_router(meta.router)
app.include_router(events.router)
tasks.register_detail_route()
app.include_router(tasks.router)
app.include_router(analytics.router)
app.include_router(settings.router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")
