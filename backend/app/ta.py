"""Technical analysis utilities for the Ultimate Bot backend.

This module implements EMA, RMA, ATR, ADX and Donchian channels. All
indicator functions are dependency‑free and operate on lists of numbers
or OHLC dictionaries.
"""

from collections.abc import Sequence
from typing import List, Optional, Dict, Any


def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out: List[Optional[float]] = [None] * len(values)
    e = float(values[0])
    out[0] = e
    for i in range(1, len(values)):
        v = float(values[i])
        e = v * k + e * (1.0 - k)
        out[i] = e
    return out


def rma(values: Sequence[float], period: int) -> List[Optional[float]]:
    n = len(values)
    if n == 0 or period < 1:
        return []
    out: List[Optional[float]] = [None] * n
    if n <= period:
        return out
    avg = sum(values[1 : period + 1]) / period
    out[period] = avg
    a = 1.0 / period
    for i in range(period + 1, n):
        avg = a * values[i] + (1.0 - a) * avg
        out[i] = avg
    return out


def atr(ohlc: List[Dict[str, Any]], period: int = 14) -> List[Optional[float]]:
    n = len(ohlc)
    if n == 0:
        return []
    tr = [None] * n
    for i in range(n):
        c = ohlc[i]
        if i == 0:
            tr[i] = c["high"] - c["low"]
        else:
            pc = ohlc[i - 1]["close"]
            hl = c["high"] - c["low"]
            hc = abs(c["high"] - pc)
            lc = abs(c["low"] - pc)
            tr[i] = max(hl, hc, lc)
    # Fill Nones with 0.0 before EMA
    return ema([t if t is not None else 0.0 for t in tr], period)


def adx(ohlc: List[Dict[str, Any]], period: int = 14) -> List[Optional[float]]:
    n = len(ohlc)
    if n < period + 2:
        return [None] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up = ohlc[i]["high"] - ohlc[i - 1]["high"]
        dn = ohlc[i - 1]["low"] - ohlc[i]["low"]
        plus_dm[i] = up if (up > 0 and up > dn) else 0.0
        minus_dm[i] = dn if (dn > 0 and dn > up) else 0.0
        hl = ohlc[i]["high"] - ohlc[i]["low"]
        hc = abs(ohlc[i]["high"] - ohlc[i - 1]["close"])
        lc = abs(ohlc[i]["low"] - ohlc[i - 1]["close"])
        tr[i] = max(hl, hc, lc)
    atr_r = rma(tr, period)
    pdm_r = rma(plus_dm, period)
    mdm_r = rma(minus_dm, period)
    plus_di = [None] * n
    minus_di = [None] * n
    dx = [None] * n
    for i in range(n):
        if atr_r[i] is None or atr_r[i] == 0:
            continue
        plus_di[i] = 100.0 * (pdm_r[i] / atr_r[i])
        minus_di[i] = 100.0 * (mdm_r[i] / atr_r[i])
        denom = (plus_di[i] or 0) + (minus_di[i] or 0)
        if denom:
            dx[i] = 100.0 * abs((plus_di[i] or 0) - (minus_di[i] or 0)) / denom
    return rma([d if d is not None else 0.0 for d in dx], period)


def donchian(ohlc: List[Dict[str, Any]], period: int = 20) -> Dict[str, List[Optional[float]]]:
    n = len(ohlc)
    hi: List[Optional[float]] = [None] * n
    lo: List[Optional[float]] = [None] * n
    for i in range(n):
        s = max(0, i - period + 1)
        H = float("-inf")
        L = float("inf")
        for j in range(s, i + 1):
            H = max(H, ohlc[j]["high"])
            L = min(L, ohlc[j]["low"])
        hi[i] = H
        lo[i] = L
    return {"hi": hi, "lo": lo}
