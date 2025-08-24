"""API entry point (Strategy V3.4)."""

from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI, Body, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx, math, time

from .config import settings
from .engine import BotEngine
from .models import Status

engine = BotEngine()


def _http2_available() -> bool:
    try:
        import h2  # noqa
        return True
    except Exception:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient(
        http2=_http2_available(),
        follow_redirects=True,
        timeout=httpx.Timeout(connect=3.0, read=2.5, write=2.5, pool=3.0),
        limits=httpx.Limits(max_keepalive_connections=100, max_connections=100),
        headers={"User-Agent": "UltimateBot/3.4"},
    )
    await engine.start(client)
    try:
        yield
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


app = FastAPI(title="Ultimate Bot API — V3.4", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["http://localhost:5173", "http://127.0.0.1:5173"],
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
    unreal = engine.broker.mark(engine.price) if engine.price is not None else 0.0
    sod = int((int(time.time()) // 86400) * 86400)
    pnl_today = sum([t.pnl for t in engine.broker.history if (t.close_time or t.open_time) >= sod])
    fills_today = sum(1 for t in engine.broker.history if (t.close_time or t.open_time) >= sod) + (1 if pos else 0)

    reg = engine.router.last_regime
    bias = engine.router.last_bias
    adx = engine.router.last_adx
    atr = engine.router.last_atr_pct
    active = engine.router.last_strategy

    iC_m1 = len(engine.m1) - 2 if len(engine.m1) >= 2 else None
    iC_h1 = len(engine.h1) - 2 if len(engine.h1) >= 2 else None

    macd_m1_state = "flat"
    macd_h1_state = "flat"
    if engine._macd_m1[0] and iC_m1 is not None and iC_m1 < len(engine._macd_m1[0]):
        l, s = engine._macd_m1
        prev = (l[iC_m1 - 1] or 0.0) - (s[iC_m1 - 1] or 0.0)
        cur = (l[iC_m1] or 0.0) - (s[iC_m1] or 0.0)
        macd_m1_state = "cross" if (prev <= 0 < cur or prev >= 0 > cur) else ("up" if cur > 0 else "down" if cur < 0 else "flat")
    if engine._macd_h1[0] and iC_h1 is not None and iC_h1 < len(engine._macd_h1[0]):
        l, s = engine._macd_h1
        prev = (l[iC_h1 - 1] or 0.0) - (s[iC_h1 - 1] or 0.0)
        cur = (l[iC_h1] or 0.0) - (s[iC_h1] or 0.0)
        macd_h1_state = "cross" if (prev <= 0 < cur or prev >= 0 > cur) else ("up" if cur > 0 else "down" if cur < 0 else "flat")

    rsi_m1 = engine._rsi_m1[iC_m1] if (engine._rsi_m1 and iC_m1 is not None and iC_m1 < len(engine._rsi_m1)) else None
    rsi_h1 = engine._rsi_h1[iC_h1] if (engine._rsi_h1 and iC_h1 is not None and iC_h1 < len(engine._rsi_h1)) else None

    # Day-lock & fast-tape UI flags
    taker_fails = len([t for t in engine._taker_fail_events if int(time.time()) - t <= settings.spec.FAST_TAPE_DISABLE_WINDOW_MIN * 60])
    day_lock_armed = 1 if engine._day_lock_armed else 0
    day_lock_floor = engine._day_lock_floor_pct

    return Status(
        price=_fmt(engine.price, 2),
        bid=_fmt(engine.bid, 2),
        ask=_fmt(engine.ask, 2),
        status=engine.status_text,
        equity=_fmt(engine.broker.equity, 2) or 0.0,
        pos=pos,
        history=engine.broker.history[-100:],
        candles=[c for c in engine.m1[-150:]],
        strategy="Strategy V3.4",
        activeStrategy=active,
        regime=reg, bias=bias, adx=_fmt(adx, 0), atrPct=_fmt(atr, 4),
        rsiM1=_fmt(rsi_m1, 1), rsiH1=_fmt(rsi_h1, 1),
        macdM1=macd_m1_state, macdH1=macd_h1_state,
        fillsToday=fills_today, pnlToday=_fmt(pnl_today, 2) or 0.0, unrealNet=_fmt(unreal, 2) or 0.0,
        vs=_fmt(engine.VS, 2), ps=_fmt(engine.PS, 2), lossStreak=_fmt(engine._loss_streak, 1) or 0.0,
        spreadBps=_fmt(engine._last_spread_bps, 2), feeToTp=_fmt(engine._last_fee_to_tp, 3),
        slipEst=_fmt(engine._last_slip_est, 2), top3DepthNotional=_fmt(engine._synthetic_top3_notional, 0),
        dayLockArmed=day_lock_armed, dayLockFloorPct=_fmt(day_lock_floor, 2),
        # FIX: compute L2 before L1 so L2 can show
        redDayLevel=2 if engine._day_pnl_pct() <= settings.spec.RED_DAY_L2_PCT else (1 if engine._day_pnl_pct() <= settings.spec.RED_DAY_L1_PCT else 0),
        fastTapeDisabled=1 if (int(time.time()) < engine._fast_tape_disabled_until) else 0,
        takerFailCount30m=taker_fails,
        autoTrade=bool(engine.settings.get("auto_trade", False)),
    )


@app.get("/logs")
def get_logs(limit: int = Query(200, ge=1, le=500)) -> dict:
    return {"ok": True, "logs": engine.logs[-limit:]}


@app.post("/settings")
def update_settings(payload: dict = Body(...)) -> dict:
    if "macroPause" in payload:
        on = bool(payload["macroPause"])
        engine.settings["macro_pause"] = on
        if on:
            engine._macro_until = int(time.time()) + settings.spec.MACRO_PAUSE_MIN * 60
    if "autoTrade" in payload:
        on = bool(payload["autoTrade"])
        engine.settings["auto_trade"] = on
        engine.status_text = "Waiting setup" if on else "Off"
    return {"ok": True, "settings": engine.settings}


@app.post("/start")
def start_bot() -> dict:
    engine.settings["auto_trade"] = True
    engine.status_text = "Waiting setup"
    return {"ok": True}


@app.post("/stop")
def stop_bot() -> dict:
    engine.settings["auto_trade"] = False
    engine.status_text = "Off"
    return {"ok": True}


# Server-side API key bucket (kept for UI compatibility)
_api_keys_store = {"apiKey": "", "apiSecret": ""}

@app.post("/apikeys")
def save_apikeys(payload: dict = Body(...)) -> dict:
    _api_keys_store["apiKey"] = str(payload.get("apiKey") or "")
    _api_keys_store["apiSecret"] = str(payload.get("apiSecret") or "")
    return {"ok": True}
