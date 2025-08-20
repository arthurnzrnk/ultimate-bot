"""Market data fetching utilities for the Ultimate Bot.

This version:
- Seeds history with multiple fallbacks: Binance → Coinbase Exchange → local cache.
- Caches last successful seed to disk so the UI has candles instantly on launch.
- Keeps the existing robust live tick polling (Coinbase with Binance fallbacks).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Tuple, List

import httpx

from .models import Candle

# ----------------------
# Endpoints for seeding
# ----------------------

# Binance API endpoints for seeding historical candles
BINANCE_1M = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit="
BINANCE_1H = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit="

# Coinbase *Exchange* (the former "Pro") candles: [ time, low, high, open, close, volume ]
CBX_1M = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60&limit=300"
CBX_1H = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600&limit=400"

# ----------------------
# Endpoints for live tick polling (unchanged)
# ----------------------
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
COINBASE_BUY = "https://api.coinbase.com/v2/prices/BTC-USD/buy"
COINBASE_SELL = "https://api.coinbase.com/v2/prices/BTC-USD/sell"

BINANCE_BOOK = "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT"
BINANCE_SPOT_TICK = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

# ----------------------
# Local cache
# ----------------------
CACHE_DIR = Path(__file__).resolve().parent / "_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_M1 = CACHE_DIR / "m1.json"
CACHE_H1 = CACHE_DIR / "h1.json"


# ------------- helpers -------------

async def fetch_json(client: httpx.AsyncClient, url: str):
    """Fetch JSON from an endpoint with error handling."""
    try:
        # add ts to bypass any edge caches
        sep = "&" if "?" in url else "?"
        r = await client.get(f"{url}{sep}ts={int(time.time() * 1000)}",
                             timeout=10.0,
                             headers={"User-Agent": "UltimateBot/1.0"})
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _to_candles_from_binance(raw: list) -> List[Candle]:
    """Parse Binance klines list -> List[Candle]."""
    out: List[Candle] = []
    if isinstance(raw, list):
        for k in raw:
            try:
                out.append(
                    Candle(
                        time=int(k[0] // 1000),
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5]),
                    )
                )
            except Exception:
                continue
    return out


def _to_candles_from_cbx(raw: list) -> List[Candle]:
    """
    Parse Coinbase Exchange candles (array of arrays):
    Each entry: [ time, low, high, open, close, volume ] (time is seconds)
    Returned newest→oldest; we sort ascending by time.
    """
    out: List[Candle] = []
    if isinstance(raw, list):
        for k in raw:
            try:
                t, lo, hi, op, cl, vol = k
                out.append(
                    Candle(
                        time=int(t),
                        open=float(op),
                        high=float(hi),
                        low=float(lo),
                        close=float(cl),
                        volume=float(vol),
                    )
                )
            except Exception:
                continue
    # Coinbase returns newest first; sort ascending
    out.sort(key=lambda c: c.time)
    return out


def _save_cache(m1: List[Candle], h1: List[Candle]) -> None:
    try:
        with open(CACHE_M1, "w") as f:
            json.dump([c.model_dump() for c in m1], f)
        with open(CACHE_H1, "w") as f:
            json.dump([c.model_dump() for c in h1], f)
    except Exception:
        pass


def _load_cache() -> Tuple[List[Candle], List[Candle]]:
    def load_one(p: Path) -> List[Candle]:
        try:
            if p.exists():
                data = json.loads(p.read_text())
                out = []
                for c in data:
                    try:
                        out.append(Candle(**c))
                    except Exception:
                        continue
                return out
        except Exception:
            pass
        return []
    return load_one(CACHE_M1), load_one(CACHE_H1)


# ------------- seeding -------------

async def seed_klines(client: httpx.AsyncClient) -> tuple[list[Candle], list[Candle], str]:
    """
    Seed the m1 and h1 candles with robust fallbacks.

    Returns:
        (m1, h1, source) where source is 'binance' | 'coinbase' | 'cache' | 'none'
    """
    # 1) Try Binance
    m1_raw = await fetch_json(client, BINANCE_1M + "300")
    h1_raw = await fetch_json(client, BINANCE_1H + "400")
    m1 = _to_candles_from_binance(m1_raw) if m1_raw else []
    h1 = _to_candles_from_binance(h1_raw) if h1_raw else []
    if m1 and h1:
        _save_cache(m1, h1)
        return m1, h1, "binance"

    # 2) Try Coinbase Exchange candles
    cbx_m1 = await fetch_json(client, CBX_1M)
    cbx_h1 = await fetch_json(client, CBX_1H)
    m1 = _to_candles_from_cbx(cbx_m1) if cbx_m1 else []
    h1 = _to_candles_from_cbx(cbx_h1) if cbx_h1 else []
    if m1 and h1:
        _save_cache(m1, h1)
        return m1, h1, "coinbase"

    # 3) Fall back to cache (from last successful session)
    m1_c, h1_c = _load_cache()
    if m1_c or h1_c:
        # If only one exists in cache, return what we have;
        # the engine will aggregate as ticks come in.
        return (m1_c or []), (h1_c or []), "cache"

    # 4) Nothing worked
    return [], [], "none"


# ------------- tick polling (unchanged) -------------

def _normalize_bid_ask_from_spot(px: float | None, bid: float | None, ask: float | None) -> tuple[float | None, float | None]:
    """Retail 'buy'/'sell' may include padded spreads. Clamp to a tight band around spot if needed."""
    if px is None and (bid is None or ask is None):
        return bid, ask
    if px is not None:
        if bid is None:
            bid = px * (1.0 - 0.0004)
        if ask is None:
            ask = px * (1.0 + 0.0004)
        if ask and bid:
            wid = (ask - bid) / max(1e-12, px)
            if wid > 0.003:
                bid = px * (1.0 - 0.0004)
                ask = px * (1.0 + 0.0004)
    return bid, ask


async def _poll_coinbase_tick(client: httpx.AsyncClient) -> tuple[float | None, float | None, float | None]:
    """Poll Coinbase spot, buy and sell endpoints for a tick."""
    ts = int(time.time() * 1000)
    sp, bu, se = await asyncio.gather(
        fetch_json(client, COINBASE_SPOT + f"?ts={ts}"),
        fetch_json(client, COINBASE_BUY + f"?ts={ts}"),
        fetch_json(client, COINBASE_SELL + f"?ts={ts}"),
    )
    px = float(sp["data"]["amount"]) if sp and "data" in sp else None
    bid = float(se["data"]["amount"]) if se and "data" in se else None
    ask = float(bu["data"]["amount"]) if bu and "data" in bu else None
    bid, ask = _normalize_bid_ask_from_spot(px, bid, ask)
    return px, bid, ask


async def poll_tick(client: httpx.AsyncClient) -> tuple[float | None, float | None, float | None]:
    """Robust tick polling with fallbacks (Coinbase → Binance bookTicker → Binance spot)."""
    try:
        px, bid, ask = await _poll_coinbase_tick(client)
        if px is not None and bid is not None and ask is not None:
            return px, bid, ask
    except Exception:
        pass

    try:
        bj = await fetch_json(client, BINANCE_BOOK)
        if isinstance(bj, dict) and "bidPrice" in bj and "askPrice" in bj:
            b = float(bj["bidPrice"])
            a = float(bj["askPrice"])
            px2 = (b + a) / 2.0
            b, a = _normalize_bid_ask_from_spot(px2, b, a)
            return px2, b, a
    except Exception:
        pass

    sj = await fetch_json(client, BINANCE_SPOT_TICK)
    p = float(sj["price"]) if isinstance(sj, dict) and "price" in sj else None
    b, a = _normalize_bid_ask_from_spot(p, None, None)
    return p, b, a
