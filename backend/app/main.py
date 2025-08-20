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

    reg = engine.router.last_regime
    bias = engine.router.last_bias
    adx = engine.router.last_adx
    atr = engine.router.last_atr_pct
    active = engine.router.last_strategy

    atr_band_min = engine.profile.get("ATR_PCT_MIN")
    atr_band_max = engine.profile.get("ATR_PCT_MAX")

    # Always return a fixed window so the UI never starts at 1 bar
    def _candles_fixed(n=150):
        cs = [c for c in engine.m1[-n:]]
        if len(cs) >= n:
            return cs
        if cs:
            first = cs[0]
            missing = n - len(cs)
            pad = []
            for k in range(missing, 0, -1):
                pad.append({
                    "time": first["time"] - k * 60,
                    "open": first["open"],
                    "high": first["high"],
                    "low": first["low"],
                    "close": first["close"],
                    "volume": first.get("volume", 0.0),
                })
            return pad + cs
        return cs

    return Status(
        price=_fmt(engine.price, 2),
        bid=_fmt(engine.bid, 2),
        ask=_fmt(engine.ask, 2),
        status=engine.status_text,
        equity=_fmt(engine.broker.equity, 2) or 0.0,
        pos=pos,
        history=engine.broker.history[-100:],
        candles=_candles_fixed(150),
        scalpMode=engine.settings.get("scalp_mode", True),
        autoTrade=engine.settings.get("auto_trade", False),
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
        atrBandMin=_fmt(atr_band_min, 4),
        atrBandMax=_fmt(atr_band_max, 4),
    )

@app.get("/logs")
def get_logs(limit: int = Query(200, ge=1, le=500)) -> dict:
    return {"ok": True, "logs": engine.logs[-limit:]}

@app.post("/settings")
def update_settings(payload: dict = Body(...)) -> dict:
    if "profileMode" in payload:
        v = str(payload["profileMode"]).upper()
        if v not in ("AUTO", "LIGHT", "HEAVY"):
            v = "AUTO"
        engine.settings["profile_mode"] = v
    if "macroPause" in payload:
        engine.settings["macro_pause"] = bool(payload["macroPause"])
    if "scalpMode" in payload:
        engine.settings["scalp_mode"] = bool(payload["scalpMode"])
    if "autoTrade" in payload:
        engine.settings["auto_trade"] = bool(payload["autoTrade"])
    if "strategy" in payload:
        engine.settings["strategy"] = str(payload["strategy"])

    # --- sizing controls ---
    if "sizingMode" in payload:
        v = str(payload["sizingMode"]).upper()
        if v not in ("RISK", "ALL_IN", "NOTIONAL_PCT"):
            v = "RISK"
        engine.settings["sizing_mode"] = v
    if "allInLeverage" in payload:
        try:
            engine.settings["all_in_leverage"] = float(payload["allInLeverage"])
        except Exception:
            pass
    if "notionalPct" in payload:
        try:
            engine.settings["notional_pct"] = float(payload["notionalPct"])
        except Exception:
            pass

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

# ---------------- DEBUG HELPERS ----------------

# Open a tiny paper position (used to prove broker/UI path)
@app.post("/debug/open_test")
def open_test(payload: dict = Body({"side": "BUY", "tf": "m1", "risk_usd": 5.0})):
    side = str(payload.get("side", "BUY")).upper()
    tf = "h1" if str(payload.get("tf", "m1")).lower() == "h1" else "m1"
    risk_usd = float(payload.get("risk_usd", 5.0))
    if engine.price is None:
        return {"ok": False, "error": "No price yet."}
    entry = engine.price
    stopd = entry * 0.002  # 0.20%
    taked = entry * 0.002
    qty = max(0.0001, risk_usd / max(1.0, stopd))
    stop = entry - stopd if side == "BUY" else entry + stopd
    take = entry + taked if side == "BUY" else entry - taked

    if engine.broker.pos:
        ps = engine.broker.pos.side
        if (side == "BUY" and ps == "short") or (side == "SELL" and ps == "long"):
            engine.broker.close(entry)

    if not engine.broker.pos:
        engine.broker.open(
            side, entry, qty, stop, take, stopd, maker=True,
            tf=tf, profile=engine.profile_active, scratch_after_sec=300,
        )
        engine.logs.append({"ts": int(time.time()), "text": f"DEBUG: Opened test {side} @ {entry:.2f} qty={qty:.6f}"})
    return {"ok": True, "pos": engine.broker.pos}

# Close whatever is open at the current price (market-flat)
@app.post("/debug/flat")
def debug_flat() -> dict:
    p = engine.broker.pos
    if not p:
        return {"ok": True, "msg": "No open position"}
    px = engine.price or p.entry
    net = engine.broker.close(px)
    engine.logs.append({"ts": int(time.time()), "text": f"DEBUG: Flat @ {px:.2f} PnL {net:+.2f}"})
    return {"ok": True, "pnl": _fmt(net, 2)}

# Partial close by fraction at current price (e.g., 0.5 = close 50%)
@app.post("/debug/partial")
def debug_partial(payload: dict = Body({"fraction": 0.5})):
    p = engine.broker.pos
    if not p:
        return {"ok": False, "error": "No open position"}
    frac = float(payload.get("fraction", 0.5))
    frac = max(0.01, min(0.99, frac))
    px = engine.price or p.entry
    net = engine.broker.partial_close(frac, px)
    engine.logs.append({"ts": int(time.time()), "text": f"DEBUG: Partial {frac*100:.0f}% @ {px:.2f} PnL { (net or 0.0):+.2f}"})
    return {"ok": True, "pnl": _fmt(net or 0.0, 2), "fraction": frac}
