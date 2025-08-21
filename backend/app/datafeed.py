"""Market data fetching utilities for the Ultimate Bot.

This version hardens the live ticker cadence:
- Races 6 providers concurrently (CBX Exchange ticker + Coinbase retail spot + Binance book + Binance spot + Kraken + Bitstamp).
- Tight per‑request timeouts; cancels stragglers so the loop stays near 1 Hz.
- Prefers real bid/ask from Binance book or CBX ticker; synthesizes from spot if needed.
- Keeps robust multi‑source seeding with a local cache fallback.
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

BINANCE_1M = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit="
BINANCE_1H = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit="

# Coinbase Exchange (candles: [time, low, high, open, close, volume])
CBX_1M = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60&limit=300"
CBX_1H = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600&limit=400"

# ----------------------
# Endpoints for live tick polling (concurrent race)
# ----------------------
CBX_TICKER = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"  # {"price","bid","ask","time"}
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/BTC-USD/spot"         # {"data":{"amount": "..."}}
BINANCE_BOOK = "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT"  # {"bidPrice","askPrice"}
BINANCE_SPOT_TICK = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"  # {"price": "..."}
KRAKEN_TICKER = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"      # result.<pair>.b[0], a[0], c[0]
BITSTAMP_TICKER = "https://www.bitstamp.net/api/v2/ticker/btcusd/"        # {"last","bid","ask"}

# ----------------------
# Local cache
# ----------------------
CACHE_DIR = Path(__file__).resolve().parent / "_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_M1 = CACHE_DIR / "m1.json"
CACHE_H1 = CACHE_DIR / "h1.json"


# ------------- helpers -------------

async def fetch_json(client: httpx.AsyncClient, url: str, *, timeout: float = 1.0):
    """Fetch JSON with a tight per-request timeout and a cache‑busting ts."""
    try:
        sep = "&" if "?" in url else "?"
        r = await client.get(
            f"{url}{sep}ts={int(time.time() * 1000)}",
            timeout=timeout,
            headers={"User-Agent": "UltimateBot/1.2"},
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _to_candles_from_binance(raw: list) -> List[Candle]:
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
    m1_raw = await fetch_json(client, BINANCE_1M + "300", timeout=3.0)
    h1_raw = await fetch_json(client, BINANCE_1H + "400", timeout=3.0)
    m1 = _to_candles_from_binance(m1_raw) if m1_raw else []
    h1 = _to_candles_from_binance(h1_raw) if h1_raw else []
    if m1 and h1:
        _save_cache(m1, h1)
        return m1, h1, "binance"

    # 2) Try Coinbase Exchange candles
    cbx_m1 = await fetch_json(client, CBX_1M, timeout=3.0)
    cbx_h1 = await fetch_json(client, CBX_1H, timeout=3.0)
    m1 = _to_candles_from_cbx(cbx_m1) if cbx_m1 else []
    h1 = _to_candles_from_cbx(cbx_h1) if cbx_h1 else []
    if m1 and h1:
        _save_cache(m1, h1)
        return m1, h1, "coinbase"

    # 3) Cache fallback
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
            if wid > 0.003:  # cap synthetic spread at 30 bps
                bid = px * (1.0 - 0.0004)
                ask = px * (1.0 + 0.0004)
    return bid, ask


def _parse_kraken(j: dict) -> tuple[float | None, float | None, float | None]:
    try:
        res = j.get("result") or {}
        if not res:
            return None, None, None
        # take the first pair key regardless of the exact name
        k = next(iter(res.keys()))
        d = res[k]
        bid = float(d["b"][0]) if d.get("b") else None
        ask = float(d["a"][0]) if d.get("a") else None
        px = float(d["c"][0]) if d.get("c") else None
        return px, bid, ask
    except Exception:
        return None, None, None


def _parse_bitstamp(j: dict) -> tuple[float | None, float | None, float | None]:
    try:
        px = float(j["last"]) if "last" in j else None
        bid = float(j["bid"]) if "bid" in j else None
        ask = float(j["ask"]) if "ask" in j else None
        return px, bid, ask
    except Exception:
        return None, None, None


async def poll_tick(client: httpx.AsyncClient) -> tuple[float | None, float | None, float | None]:
    """
    Race CBX Exchange ticker + Coinbase spot + Binance book + Binance spot + Kraken + Bitstamp.
    Returns (px, bid, ask) fast; cancels stragglers to keep cadence ≈ 1 Hz.
    """
    ts = int(time.time() * 1000)

    tasks = [
        asyncio.create_task(fetch_json(client, CBX_TICKER + f"?ts={ts}", timeout=0.9)),
        asyncio.create_task(fetch_json(client, COINBASE_SPOT + f"?ts={ts}", timeout=0.9)),
        asyncio.create_task(fetch_json(client, BINANCE_BOOK, timeout=0.9)),
        asyncio.create_task(fetch_json(client, BINANCE_SPOT_TICK, timeout=0.9)),
        asyncio.create_task(fetch_json(client, KRAKEN_TICKER + f"?ts={ts}", timeout=0.9)),
        asyncio.create_task(fetch_json(client, BITSTAMP_TICKER + f"?ts={ts}", timeout=0.9)),
    ]

    px: float | None = None
    bid: float | None = None
    ask: float | None = None

    try:
        # Phase 1: very quick wait for the first responders
        done, pending = await asyncio.wait(set(tasks), timeout=0.8, return_when=asyncio.FIRST_COMPLETED)

        def absorb(res: dict | None):
            nonlocal px, bid, ask
            if not isinstance(res, dict):
                return
            # Binance book (authoritative bid/ask)
            if "bidPrice" in res and "askPrice" in res:
                try:
                    b = float(res["bidPrice"])
                    a = float(res["askPrice"])
                    bid, ask = _normalize_bid_ask_from_spot(((b + a) / 2.0), b, a)
                    px = (bid + ask) / 2.0 if (bid and ask) else px
                    return
                except Exception:
                    pass
            # Coinbase Exchange ticker
            if "price" in res and ("bid" in res or "ask" in res):
                try:
                    p = float(res["price"])
                    b = float(res["bid"]) if res.get("bid") is not None else None
                    a = float(res["ask"]) if res.get("ask") is not None else None
                    if b is not None or a is not None:
                        bb, aa = _normalize_bid_ask_from_spot(p, b, a)
                        bid, ask = bb, aa
                        px = (bid + ask) / 2.0 if (bid and ask) else p
                    else:
                        px = p
                    return
                except Exception:
                    pass
            # Coinbase retail spot
            if "data" in res and isinstance(res["data"], dict) and "amount" in res["data"]:
                try:
                    px = float(res["data"]["amount"])
                    return
                except Exception:
                    pass
            # Binance spot
            if "price" in res and "bidPrice" not in res:
                try:
                    px = float(res["price"])
                    return
                except Exception:
                    pass
            # Kraken
            if "result" in res:
                p2, b2, a2 = _parse_kraken(res)
                if b2 is not None or a2 is not None:
                    bb, aa = _normalize_bid_ask_from_spot(p2, b2, a2)
                    bid, ask = bb, aa
                    px = (bid + ask) / 2.0 if (bid and ask) else (p2 or px)
                    return
                if p2 is not None:
                    px = p2
                    return
            # Bitstamp
            if "last" in res:
                p3, b3, a3 = _parse_bitstamp(res)
                if b3 is not None or a3 is not None:
                    bb, aa = _normalize_bid_ask_from_spot(p3, b3, a3)
                    bid, ask = bb, aa
                    px = (bid + ask) / 2.0 if (bid and ask) else (p3 or px)
                    return
                if p3 is not None:
                    px = p3
                    return

        # Absorb anything that finished already
        for t in tasks:
            if t in done:
                absorb(t.result())

        # If we already have bid/ask, return immediately
        if bid is not None and ask is not None:
            return ((bid + ask) / 2.0), bid, ask
        # Otherwise synthesize from spot if present
        if px is not None:
            b, a = _normalize_bid_ask_from_spot(px, None, None)
            return px, b, a

        # Phase 2: tiny extra window to grab one more response
        if pending:
            more_done, _ = await asyncio.wait(pending, timeout=0.35, return_when=asyncio.FIRST_COMPLETED)
            for t in more_done:
                absorb(t.result())

            if bid is not None and ask is not None:
                return ((bid + ask) / 2.0), bid, ask
            if px is not None:
                b, a = _normalize_bid_ask_from_spot(px, None, None)
                return px, b, a

        # Total failure this tick; caller will try again next loop.
        return None, None, None

    finally:
        # Ensure pending HTTP requests don't pile up.
        for t in tasks:
            if not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
