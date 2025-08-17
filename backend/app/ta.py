"""Technical analysis utilities for the Ultimate Bot backend.

This module implements basic indicators such as exponential moving averages
(EMA), rolling moving averages (RMA), average true range (ATR), the average
directional index (ADX) and Donchian channels. Keeping these calculations
pure and dependency‑free makes the trading engine easier to test and
maintain. Indicator functions operate on lists of numbers or dictionaries
representing OHLC candles.
"""

from collections.abc import Sequence
from typing import List, Tuple, Optional


def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    """Compute an exponential moving average.

    Args:
        values: Sequence of numerical values.
        period: The EMA period.

    Returns:
        A list of floats (or ``None`` for undefined entries) representing
        the EMA for each element in the input sequence.
    """
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
    """Compute a rolling moving average.

    Unlike a simple moving average, the RMA uses an exponential weighting
    scheme that is similar to EMA but with a different smoothing factor.

    Args:
        values: Sequence of numerical values.
        period: The RMA period.

    Returns:
        A list of floats (or ``None``) representing the RMA for each
        element. Values before the period index are ``None``.
    """
    n = len(values)
    if n == 0 or period < 1:
        return []
    out: List[Optional[float]] = [None] * n
    if n <= period:
        return out
    avg = sum(values[1: period + 1]) / period
    out[period] = avg
    a = 1.0 / period
    for i in range(period + 1, n):
        avg = a * values[i] + (1.0 - a) * avg
        out[i] = avg
    return out


def atr(ohlc: List[dict], period: int = 14) -> List[Optional[float]]:
    """Calculate the Average True Range.

    ATR measures market volatility using high, low and close values. The
    implementation here uses the EMA smoothing factor for the true range.

    Args:
        ohlc: List of candles with 'high', 'low', 'close'.
        period: Lookback period for the ATR.

    Returns:
        A list of ATR values with the same length as ``ohlc``.
    """
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
    return ema([t if t is not None else 0.0 for t in tr], period)


def adx(ohlc: List[dict], period: int = 14) -> List[Optional[float]]:
    """Calculate the Average Directional Index (ADX).

    ADX indicates trend strength, regardless of direction. Values below
    20 often indicate a ranging market, while values above 25 suggest a
    trending market.

    Args:
        ohlc: List of candles with 'high', 'low', and 'close' keys.
        period: Lookback period for ADX.

    Returns:
        A list of ADX values corresponding to ``ohlc`` length.
    """
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


def donchian(ohlc: List[dict], period: int = 100) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Compute Donchian channel high and low bands.

    A Donchian channel defines the upper and lower boundaries of price
    movement over a given period. The high band is the highest high, and
    the low band is the lowest low over the lookback window.

    Args:
        ohlc: List of candles with 'high' and 'low' keys.
        period: Number of bars to look back for band calculation.

    Returns:
        A tuple of two lists: the high band and the low band.
    """
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
    return hi, lo