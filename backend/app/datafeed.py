"""Market data fetching utilities for the Ultimate Bot.

This version fixes the "frozen price / no orders" issue by:
- Racing providers concurrently (Coinbase spot + Binance book + Binance spot).
- Using tight per‑request timeouts and cancelling stragglers.
- Preferring Binance order book (real bid/ask); synthesizing bid/ask from spot when needed.
- Keeping the robust multi‑source seeding with a local cache fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
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
# Endpoints for live tick polling (concurrent race)
# ----------------------
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
# (we no longer wait on Coinbase buy/sell — we synthesize when needed)
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

async def fetch_json(client: httpx.AsyncClient, url: str, *, timeout: float = 1.6):
    """Fetch JSON with a tight per-request timeout and a cache‑busting ts."""
    try:
        sep = "&" if "?" in url else "?"
        r = await client.get(
            f"{url}{sep}ts={int(time.time() * 1000)}",
            timeout=timeout,
            headers={"User-Agent": "UltimateBot/1.1"},
        )
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
    m1_raw = await fetch_json(client, BINANCE_1M + "300", timeout=3.5)
    h1_raw = await fetch_json(client, BINANCE_1H + "400", timeout=3.5)
    m1 = _to_candles_from_binance(m1_raw) if m1_raw else []
    h1 = _to_candles_from_binance(h1_raw) if h1_raw else []
    if m1 and h1:
        _save_cache(m1, h1)
        return m1, h1, "binance"

    # 2) Try Coinbase Exchange candles
    cbx_m1 = await fetch_json(client, CBX_1M, timeout=3.5)
    cbx_h1 = await fetch_json(client, CBX_1H, timeout=3.5)
    m1 = _to_candles_from_cbx(cbx_m1) if cbx_m1 else []
    h1 = _to_candles_from_cbx(cbx_h1) if cbx_h1 else []
    if m1 and h1:
        _save_cache(m1, h1)
        return m1, h1, "coinbase"

    # 3) Fall back to cache (from last successful session)
    m1_c, h1_c = _load_cache()
    if m1_c or h1_c:
        return (m1_c or []), (h1_c or []), "cache"

    # 4) Nothing worked
    return [], [], "none"


# ------------- tick polling -------------

def _normalize_bid_ask_from_spot(px: float | None, bid: float | None, ask: float | None) -> tuple[float | None, float | None]:
    """Clamp synthetic bid/ask to a tight band around spot if retail endpoints pad spreads."""
    if px is None and (bid is None or ask is None):
        return bid, ask
    if px is not None:
        if bid is None:
            bid = px * (1.0 - 0.0004)
        if ask is None:
            ask = px * (1.0 + 0.0004)
        if ask and bid:
            wid = (ask - bid) / max(1e-12, px)
            if wid > 0.003:  # 30 bps cap on synthetic spread
                bid = px * (1.0 - 0.0004)
                ask = px * (1.0 + 0.0004)
    return bid, ask


async def poll_tick(client: httpx.AsyncClient) -> tuple[float | None, float | None, float | None]:
    """
    Race Coinbase spot + Binance order book + Binance spot.
    Returns (px, bid, ask) quickly; cancels stragglers to keep 1 Hz rhythm.
    """
    ts = int(time.time() * 1000)

    # Fire all three; whichever finishes first gives us a viable tick.
    t_cb_spot = asyncio.create_task(fetch_json(client, COINBASE_SPOT + f"?ts={ts}", timeout=1.5))
    t_bn_book = asyncio.create_task(fetch_json(client, BINANCE_BOOK, timeout=1.5))
    t_bn_spot = asyncio.create_task(fetch_json(client, BINANCE_SPOT_TICK, timeout=1.5))

    px: float | None = None
    bid: float | None = None
    ask: float | None = None

    try:
        # First shot: wait briefly for any to come back.
        done, pending = await asyncio.wait(
            {t_cb_spot, t_bn_book, t_bn_spot},
            timeout=1.2,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Collect whatever finished.
        for task in (t_cb_spot, t_bn_book, t_bn_spot):
            if not task.done():
                continue
            res = task.result()
            if task is t_bn_book and isinstance(res, dict) and "bidPrice" in res and "askPrice" in res:
                try:
                    b = float(res["bidPrice"])
                    a = float(res["askPrice"])
                    bid, ask = _normalize_bid_ask_from_spot(((b + a) / 2.0), b, a)
                    px = (bid + ask) / 2.0 if (bid and ask) else px
                except Exception:
                    pass
            elif task is t_cb_spot and isinstance(res, dict) and "data" in res:
                try:
                    px = float(res["data"]["amount"])
                except Exception:
                    pass
            elif task is t_bn_spot and isinstance(res, dict) and "price" in res:
                try:
                    px = float(res["price"])
                except Exception:
                    pass

        # If Binance book gave us bid/ask, we are done.
        if bid is not None and ask is not None:
            return ((bid + ask) / 2.0), bid, ask

        # Otherwise synthesize bid/ask from any spot we have.
        if px is not None:
            b, a = _normalize_bid_ask_from_spot(px, None, None)
            return px, b, a

        # If nothing finished yet, wait the remainder once more very briefly.
        if pending:
            more_done, _ = await asyncio.wait(pending, timeout=0.4, return_when=asyncio.FIRST_COMPLETED)
            for task in more_done:
                res = task.result()
                if task is t_bn_book and isinstance(res, dict) and "bidPrice" in res and "askPrice" in res:
                    try:
                        b = float(res["bidPrice"])
                        a = float(res["askPrice"])
                        bid, ask = _normalize_bid_ask_from_spot(((b + a) / 2.0), b, a)
                        px = (bid + ask) / 2.0 if (bid and ask) else px
                    except Exception:
                        pass
                elif task is t_cb_spot and isinstance(res, dict) and "data" in res:
                    try:
                        px = float(res["data"]["amount"])
                    except Exception:
                        pass
                elif task is t_bn_spot and isinstance(res, dict) and "price" in res:
                    try:
                        px = float(res["price"])
                    except Exception:
                        pass

            if bid is not None and ask is not None:
                return ((bid + ask) / 2.0), bid, ask
            if px is not None:
                b, a = _normalize_bid_ask_from_spot(px, None, None)
                return px, b, a

        # Total failure this tick; caller will try again next loop.
        return None, None, None

    finally:
        # Ensure pending HTTP requests don't pile up.
        for t in (t_cb_spot, t_bn_book, t_bn_spot):
            if not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
