"""API entry point for the Ultimate Bot backend.

This module configures the FastAPI application, including CORS middleware,
instantiates the ``BotEngine`` and defines routes for fetching status and
updating settings. When run with Uvicorn, the app will start the engine
and begin paper trading.
"""

from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
import httpx
import math
import time
from .config import settings
from .engine import BotEngine
from .models import Status


app = FastAPI(title="Ultimate Bot API")

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

engine = BotEngine()


@app.on_event("startup")
async def startup_event() -> None:
    """Start the bot engine on application startup."""
    client = httpx.AsyncClient()
    await engine.start(client)


def _fmt(n: float | None, d: int = 2):
    """Helper to format floats for the API response."""
    if n is None or (isinstance(n, float) and (math.isnan(n) or math.isinf(n))):
        return None
    return round(n, d)


@app.get("/status", response_model=Status)
def get_status() -> Status:
    """Return a snapshot of the current engine state."""
    pos = engine.broker.pos
    unreal = engine.broker.mark(engine.price) if engine.price else 0.0
    sod = int((int(time.time()) // 86400) * 86400)
    pnl_today = sum(
        [t.pnl for t in engine.broker.history if (t.close_time or t.open_time) >= sod]
    )
    fills_today = sum(
        1 for t in engine.broker.history if (t.close_time or t.open_time) >= sod
    ) + (1 if pos else 0)
    return Status(
        price=_fmt(engine.price, 2),
        bid=_fmt(engine.bid, 2),
        ask=_fmt(engine.ask, 2),
        status=engine.status_text,
        equity=_fmt(engine.broker.equity, 2) or 0.0,
        pos=pos,
        history=engine.broker.history[-100:],
        candles=[c for c in engine.m1[-150:]],
        scalpMode=engine.settings.get("scalp_mode", True),
        autoTrade=engine.settings.get("auto_trade", True),
        strategy=engine.settings.get("strategy", "Level King — Regime"),
        fillsToday=fills_today,
        pnlToday=_fmt(pnl_today, 2) or 0.0,
        unrealNet=_fmt(unreal, 2) or 0.0,
    )


@app.post("/settings")
def update_settings(payload: dict = Body(...)) -> dict:
    """Update engine settings from the frontend."""
    if "scalpMode" in payload:
        engine.settings["scalp_mode"] = bool(payload["scalpMode"])
    if "autoTrade" in payload:
        engine.settings["auto_trade"] = bool(payload["autoTrade"])
    if "strategy" in payload:
        engine.settings["strategy"] = str(payload["strategy"])
    return {"ok": True, "settings": engine.settings}


@app.post("/start")
def start_bot() -> dict:
    """Activate auto trading."""
    engine.settings["auto_trade"] = True
    return {"ok": True}


@app.post("/stop")
def stop_bot() -> dict:
    """Deactivate auto trading."""
    engine.settings["auto_trade"] = False
    return {"ok": True}


@app.post("/apikeys")
def save_api_keys(payload: dict = Body(...)) -> dict:
    """Placeholder for saving user API keys securely."""
    # Real implementation would securely store API keys. We do not expose them.
    return {"ok": True}


@app.get("/health")
def health() -> dict:
    """Simple health check endpoint."""
    return {"ok": True, "status": engine.status_text}