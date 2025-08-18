"""API entry point for the Ultimate Bot backend."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Body, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
import math
import time
from .config import settings
from .engine import BotEngine
from .models import Status

engine = BotEngine()

@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient()
    await engine.start(client)
    try:
        yield
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

app = FastAPI(title="Ultimate Bot API", lifespan=lifespan)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

def _fmt(n: float | None, d: int = 2):
    if n is None or (isinstance(n, float) and (math.isnan(n) or math.isinf(n))):
        return None
    return round(n, d)

@app.get("/status", response_model=Status)
def get_status() -> Status:
    pos = engine.broker.pos
    unreal = engine.broker.mark(engine.price) if engine.price else 0.0
    sod = int((int(time.time()) // 86400) * 86400)
    pnl_today = sum([t.pnl for t in engine.broker.history if (t.close_time or t.open_time) >= sod])
    fills_today = sum(1 for t in engine.broker.history if (t.close_time or t.open_time) >= sod) + (1 if pos else 0)

    # Telemetry from router (may be None during warmup)
    reg = engine.router.last_regime
    bias = engine.router.last_bias
    adx = engine.router.last_adx
    atr = engine.router.last_atr_pct
    active = engine.router.last_strategy

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
        strategy=engine.settings.get("strategy", "Adaptive Router"),
        activeStrategy=active,
        regime=reg,
        bias=bias,
        adx=_fmt(adx, 0),
        atrPct=_fmt(atr, 4),
        fillsToday=fills_today,
        pnlToday=_fmt(pnl_today, 2) or 0.0,
        unrealNet=_fmt(unreal, 2) or 0.0,
        profileMode=engine.settings.get("profile_mode", "AUTO"),
        profileModeActive=engine.profile_active,
    )

@app.get("/logs")
def get_logs(limit: int = Query(200, ge=1, le=500)) -> dict:
    return {"ok": True, "logs": engine.logs[-limit:]}

@app.post("/settings")
def update_settings(payload: dict = Body(...)) -> dict:
    # Only profile pickers are exposed in UI now; others still accepted but hidden
    if "profileMode" in payload:
        v = str(payload["profileMode"]).upper()
        if v not in ("AUTO", "LIGHT", "HEAVY"):
            v = "AUTO"
        engine.settings["profile_mode"] = v
    if "macroPause" in payload:
        engine.settings["macro_pause"] = bool(payload["macroPause"])
    # keep legacy toggles to avoid breaking clients (not used in current UI)
    if "scalpMode" in payload:
        engine.settings["scalp_mode"] = bool(payload["scalpMode"])
    if "autoTrade" in payload:
        engine.settings["auto_trade"] = bool(payload["autoTrade"])
    if "strategy" in payload:
        engine.settings["strategy"] = str(payload["strategy"])
    return {"ok": True, "settings": engine.settings}

@app.post("/start")
def start_bot() -> dict:
    engine.settings["auto_trade"] = True
    return {"ok": True}

@app.post("/stop")
def stop_bot() -> dict:
    engine.settings["auto_trade"] = False
    return {"ok": True}

@app.post("/apikeys")
def save_api_keys(payload: dict = Body(...)) -> dict:
    return {"ok": True}

@app.get("/health")
def health() -> dict:
    return {"ok": True, "status": engine.status_text}
