from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text

from database import init_db, AsyncSessionLocal, engine
from models import PricingRule, SEED_PRICING, _now
from routes import events, analytics, settings


async def _seed_pricing():
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT COUNT(*) FROM pricing_rules"))
        count = result.scalar()
        if count and count > 0:
            return
    async with AsyncSessionLocal() as session:
        for model, inp, out in SEED_PRICING:
            session.add(PricingRule(model=model, input_price_per_1m=inp, output_price_per_1m=out, updated_at=_now()))
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_pricing()
    yield


app = FastAPI(title="Token Inspector", lifespan=lifespan)

app.include_router(events.router)
app.include_router(analytics.router)
app.include_router(settings.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")
