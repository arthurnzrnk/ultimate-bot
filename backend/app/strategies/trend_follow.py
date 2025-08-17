"""Simple trend-following strategy for higher timeframes.

This strategy operates on one‑hour candles and looks for Donchian channel
breakouts in the direction of the 200‑period EMA. When ADX is above 25,
a new 100‑period high or low will trigger a buy or sell signal. The stop and
take profit distances are multiples of ATR, similar to the classic Turtle
trading system.
"""

from .base import Strategy, Signal
from ..ta import ema, atr, adx, donchian


class TrendFollow(Strategy):
    """H1 trend-following breakout strategy."""

    name = "Trend Follow (H1)"

    def evaluate(self, h1: list[dict], ctx: dict) -> Signal:
        iC = ctx.get("iC")
        if iC is None or iC < ctx.get("min_bars_h1", 0):
            return Signal(type="WAIT", reason="Need history")
        if not ctx.get("daily_ok", True):
            return Signal(type="WAIT", reason="Daily cap")
        if not ctx.get("cooldown_ok", True):
            return Signal(type="WAIT", reason="Pause after recent trade")

        closes = [c["close"] for c in h1]
        ema200 = ema(closes, 200)
        a14 = atr(h1, 14)
        hi, lo = donchian(h1, 100)
        ax = adx(h1, 14)
        ema_up = (ema200[iC] or 0) > (ema200[iC - 10] or 0)
        ema_dn = (ema200[iC] or 0) < (ema200[iC - 10] or 0)
        px = closes[iC]
        bk_up = px > (hi[iC - 1] or 0)
        bk_dn = px < (lo[iC - 1] or float("inf"))
        adx_ok = (ax[iC] or 0) > 25.0
        stop = 1.0 * (a14[iC] or 0)
        take = 1.5 * (a14[iC] or 0)
        fee_r = (2 * ctx.get("fee_taker", 0.0002) * px) / max(1.0, stop)
        if fee_r > 0.15 or not adx_ok:
            return Signal(type="WAIT", reason="ADX low" if not adx_ok else "Fees>limitR")
        if ema_up and bk_up:
            return Signal(type="BUY", reason="Donchian100 up + EMA200 up", stop_dist=stop, take_dist=take, score=4)
        if ema_dn and bk_dn:
            return Signal(type="SELL", reason="Donchian100 down + EMA200 down", stop_dist=stop, take_dist=take, score=4)
        return Signal(type="WAIT", reason="Waiting Donchian break")