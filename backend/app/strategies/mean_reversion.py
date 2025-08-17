"""Simple H1 mean‑reversion strategy.

Runs only in range regimes (low ADX). Looks for price stretched away from the
Donchian mid (avg of hi/lo) by > k * ATR and fades back toward the mean.
Stops/takes are ATR‑based.
"""

from .base import Strategy, Signal
from ..ta import atr, adx, donchian


class MeanReversion(Strategy):
    name = "Mean Reversion (H1)"

    def evaluate(self, h1: list[dict], ctx: dict) -> Signal:
        # Use the explicit H1 index that the engine puts in context
        iC = ctx.get("iC_h1")
        if iC is None or iC < ctx.get("min_h1_bars", 120):
            return Signal(type="WAIT", reason="Need H1 history")

        px = h1[iC]["close"]
        a14 = atr(h1, 14)
        ax  = adx(h1, 14)
        dc  = donchian(h1, ctx.get("donchian_len", 20))

        # Trade only when trend is weak (range regime)
        if (ax[iC] or 0.0) > ctx.get("adx_range_max", 18):
            return Signal(type="WAIT", reason="Trend regime")

        hi = dc["hi"][iC]
        lo = dc["lo"][iC]
        if hi is None or lo is None:
            return Signal(type="WAIT", reason="Warmup")

        mid = 0.5 * (hi + lo)
        atr_abs = a14[iC] or 0.0
        if atr_abs <= 0:
            return Signal(type="WAIT", reason="ATR warmup")

        # How far from the mid are we, in ATRs?
        k       = ctx.get("revert_k_atr", 0.8)     # entry threshold (in ATR)
        stop_k  = ctx.get("revert_stop_atr", 0.9)  # stop (ATRs)
        take_k  = ctx.get("revert_take_atr", 1.0)  # take (ATRs)

        dist = px - mid

        # Price sufficiently below mid → fade up
        if dist <= -k * atr_abs:
            return Signal(
                type="BUY",
                reason="Below mid by >k*ATR",
                stop_dist=stop_k * atr_abs,
                take_dist=take_k * atr_abs,
                score=3,
            )

        # Price sufficiently above mid → fade down
        if dist >= k * atr_abs:
            return Signal(
                type="SELL",
                reason="Above mid by >k*ATR",
                stop_dist=stop_k * atr_abs,
                take_dist=take_k * atr_abs,
                score=3,
            )

        return Signal(type="WAIT", reason="Near mid")
