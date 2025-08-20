"""Market data fetching utilities for the Ultimate Bot.

This module provides asynchronous functions to seed historical candles from
Binance (1m and 1h candles) and to poll Coinbase's spot, buy and sell
endpoints. Coinbase retail endpoints can report padded buy/sell prices with
a wide spread; we normalize obviously bogus spreads so strategy spread gates
remain meaningful.
"""

import httpx
import asyncio
import time
from .models import Candle

# Binance API endpoints for seeding historical candles
BINANCE_1M = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit="
BINANCE_1H = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit="

# Coinbase retail endpoints used for fallback polling
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
COINBASE_BUY = "https://api.coinbase.com/v2/prices/BTC-USD/buy"
COINBASE_SELL = "https://api.coinbase.com/v2/prices/BTC-USD/sell"


async def fetch_json(client: httpx.AsyncClient, url: str):
    """Fetch JSON from an endpoint with error handling."""
    try:
        r = await client.get(url, timeout=8.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


async def seed_klines(client: httpx.AsyncClient) -> tuple[list[Candle], list[Candle]]:
    """Seed the m1 and h1 candles from Binance.

    Returns:
        A tuple (m1, h1) where each is a list of ``Candle`` instances.
    """
    m1_raw = await fetch_json(client, BINANCE_1M + "300")
    h1_raw = await fetch_json(client, BINANCE_1H + "400")
    m1: list[Candle] = []
    h1: list[Candle] = []
    if isinstance(m1_raw, list):
        for k in m1_raw:
            m1.append(
                Candle(
                    time=int(k[0] // 1000),
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                )
            )
    if isinstance(h1_raw, list):
        for k in h1_raw:
            h1.append(
                Candle(
                    time=int(k[0] // 1000),
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                )
            )
    return m1, h1


def _normalize_bid_ask_from_spot(px: float | None, bid: float | None, ask: float | None) -> tuple[float | None, float | None]:
    """Retail 'buy'/'sell' may include padded spreads. Clamp to a tight band around spot if needed."""
    if px is None and (bid is None or ask is None):
        return bid, ask
    if px is not None:
        # If either missing, synthesize around spot ~8 bps total spread.
        if bid is None:
            bid = px * (1.0 - 0.0004)
        if ask is None:
            ask = px * (1.0 + 0.0004)
        # If spread is absurd (>30 bps), clamp to ~8 bps around spot.
        if ask and bid:
            wid = (ask - bid) / max(1e-12, px)
            if wid > 0.003:
                bid = px * (1.0 - 0.0004)
                ask = px * (1.0 + 0.0004)
    return bid, ask


async def poll_coinbase_tick(client: httpx.AsyncClient) -> tuple[float | None, float | None, float | None]:
    """Poll Coinbase spot, buy and sell endpoints for a tick.

    Returns:
        A tuple (price, bid, ask) where each element may be ``None`` if the
        corresponding endpoint failed.
    """
    ts = int(time.time() * 1000)
    sp, bu, se = await asyncio.gather(
        fetch_json(client, COINBASE_SPOT + f"?ts={ts}"),
        fetch_json(client, COINBASE_BUY + f"?ts={ts}"),
        fetch_json(client, COINBASE_SELL + f"?ts={ts}"),
    )
    px = float(sp["data"]["amount"]) if sp and "data" in sp else None
    bid = float(se["data"]["amount"]) if se and "data" in se else None
    ask = float(bu["data"]["amount"]) if bu and "data" in bu else None

    # Normalize obviously padded spreads using spot as anchor
    bid, ask = _normalize_bid_ask_from_spot(px, bid, ask)
    return px, bid, ask
