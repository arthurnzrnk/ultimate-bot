"""Regime‑aware scalping strategy.

This strategy corresponds to the "Level King — Regime" mode in the
original UI. It evaluates 1‑minute candles and uses band reclaims around
VWAP for entries. It also enforces a number of gates (ATR bounds,
spread, regime filter) to avoid trading during trending conditions or
unstable markets.
"""

from .base import Strategy, Signal
from ..ta import ema, atr
from ..models import Candle


class LevelKingRegime(Strategy):
    """Scalper that trades mean reversion inside the VWAP bands."""

    name = "Level King — Regime"

    def evaluate(self, m1: list[dict], ctx: dict) -> Signal:
        iC = ctx.get("iC")
        if iC is None or iC < ctx.get("min_bars", 0):
            return Signal(type="WAIT", reason="Need short warmup")
        if not ctx.get("daily_ok", True):
            return Signal(type="WAIT", reason="Daily cap")
        if not ctx.get("cooldown_ok", True):
            return Signal(type="WAIT", reason="Pause after recent trade")
        if ctx.get("fills", 0) >= ctx.get("max_fills", 60):
            return Signal(type="WAIT", reason="Fill cap")

        px = m1[iC]["close"]
        a14 = atr(m1, 14)
        vwap = ctx["vwap"][iC] if ctx.get("vwap") else None
        if vwap is None:
            return Signal(type="WAIT", reason="VWAP warmup")

        p = ctx.get("profile", {})
        atr_min = p.get("ATR_PCT_MIN", 0.0004)
        atr_max = p.get("ATR_PCT_MAX", 0.02)
        atr_pct = (a14[iC] or 0.0) / max(1.0, px)
        if atr_pct < atr_min or atr_pct > atr_max:
            return Signal(type="WAIT", reason="ATR range")

        # Simple slope check to skip during trends
        v10 = ema(ctx["vwap"], 10)
        slope = abs((v10[iC] or vwap) - (v10[iC - 3] or vwap)) / max(1.0, px)
        slope_mx = p.get("VWAP_SLOPE_MAX", 0.00060)
        if slope > slope_mx:
            return Signal(type="WAIT", reason="Trend regime")

        bid, ask = ctx.get("bid"), ctx.get("ask")
        if bid and ask:
            spread_bps = ((ask - bid) / ((bid + ask) / 2.0)) * 10000.0
            if spread_bps > p.get("SPREAD_BPS_MAX", 10):
                return Signal(type="WAIT", reason="Spread too wide")

        tp_floor = p.get("TP_FLOOR", 0.0020)
        band_pct = max(tp_floor, 0.7 * atr_pct)
        tp_pct = max(tp_floor, 0.8 * band_pct)
        fee_r = (2 * ctx.get("fee_rate", 0.0001)) / tp_pct
        if fee_r > p.get("FEE_R_MAX", 0.25):
            return Signal(type="WAIT", reason="Fees>limitR")

        prev = m1[iC - 1] if iC > 0 else None
        vprev = ctx["vwap"][iC - 1] if iC > 0 else None
        overshoot_long = bool(prev and vprev and prev["low"] <= vprev * (1 - band_pct * 1.05))
        reclaim_long = bool(px >= vwap * (1 - band_pct * 0.70) and m1[iC]["close"] >= m1[iC]["open"])
        overshoot_short = bool(prev and vprev and prev["high"] >= vprev * (1 + band_pct * 1.05))
        reclaim_short = bool(px <= vwap * (1 + band_pct * 0.70) and m1[iC]["close"] <= m1[iC]["open"])

        if overshoot_long and reclaim_long:
            return Signal(type="BUY", reason="Band reclaim long", stop_dist=px * tp_pct, take_dist=px * tp_pct, score=4)
        if overshoot_short and reclaim_short:
            return Signal(type="SELL", reason="Band reclaim short", stop_dist=px * tp_pct, take_dist=px * tp_pct, score=4)
        return Signal(type="WAIT", reason="Inside bands")