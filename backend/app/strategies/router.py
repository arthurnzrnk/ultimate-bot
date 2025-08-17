# backend/strategies/router.py
"""Adaptive router-as-a-strategy.

This class *is* the single "Ultimate Bot" strategy:
- Detects market regime using ADX, ATR, Donchian, and VWAP slope
- Delegates to the correct sub-strategy:
    - Range (low ADX, flat slope): Level King — Regime (VWAP band scalper) or MeanReversion
    - Breakout transition: Breakout
    - Established trend: TrendFollow
It returns the delegated strategy's Signal so the engine can act.
"""

from .base import Strategy, Signal
from ..ta import adx, atr, donchian, ema
from typing import List, Dict

# Import the concrete strategies you already have
from .level_king_regime import LevelKingRegime
from .mean_reversion import MeanReversion
from .breakout import Breakout
from .trend_follow import TrendFollow


def _aggregate(ohlc: List[Dict], step_sec: int) -> List[Dict]:
    """Aggregate 1m candles to multi-minute/hour candles."""
    if not ohlc: return []
    out = []
    bucket = (ohlc[0]["time"] // step_sec) * step_sec
    cur = None
    for c in ohlc:
        b = (c["time"] // step_sec) * step_sec
        if b != bucket:
            if cur: out.append(cur)
            bucket = b
            cur = {
                "time": b,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c.get("volume", 0.0),
            }
        else:
            if cur is None:
                cur = {
                    "time": b,
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c.get("volume", 0.0),
                }
            else:
                cur["high"] = max(cur["high"], c["high"])
                cur["low"] = min(cur["low"], c["low"])
                cur["close"] = c["close"]
                cur["volume"] += c.get("volume", 0.0)
    if cur: out.append(cur)
    return out


class StrategyRouter(Strategy):
    """ONE adaptive bot that chooses among sub-strategies automatically."""

    name = "Adaptive Router"

    def __init__(self):
        # Instantiate the modules you already have
        self.scalper = LevelKingRegime()
        self.revert  = MeanReversion()
        self.breakout = Breakout()
        self.trend   = TrendFollow()
        # defaults / thresholds
        self.adx_thr = 25
        self.min_bars_m1 = 5
        self.min_bars_h1 = 60

    # engine calls this before evaluate; we simply return self so engine uses one bot
    def pick(self, scalp_mode: bool) -> "StrategyRouter":
        self._scalp_mode = bool(scalp_mode)
        return self

    def evaluate(self, ohlc: List[Dict], ctx: Dict) -> Signal:
        iC = ctx.get("iC")
        if iC is None or iC < ctx.get("min_bars", 0):
            return Signal(type="WAIT", reason="Loading...")
        p = ctx.get("profile", {}) or {}
        px = ohlc[iC]["close"]

        # --- Regime features ---
        # ADX on aggregated 5m for scalping; on given TF for H1
        if self._scalp_mode:
            m5 = _aggregate(ohlc, 300)
            a = adx(m5, 14)
            adx_val = a[-2] if len(a) >= 2 else None
        else:
            a = adx(ohlc, 14)
            adx_val = a[-2] if len(a) >= 2 else None

        # ATR% on current TF
        A = atr(ohlc, 14)
        atr_pct = (A[iC] or 0.0) / max(1.0, px)

        # Donchian for breakout/trend confirmation (use 20 for TF)
        dc = donchian(ohlc, 20)
        hi_prev = dc["hi"][iC-1] if iC > 0 else None
        lo_prev = dc["lo"][iC-1] if iC > 0 else None
        bk_up = bool(hi_prev and px > hi_prev)
        bk_dn = bool(lo_prev and px < lo_prev)

        # VWAP slope gate (skip mean-revert if slope too steep)
        slope_ok = True
        if ctx.get("vwap"):
            v10 = ema(ctx["vwap"], 10)
            vcur = v10[iC] if iC < len(v10) else ctx["vwap"][iC]
            vprev = v10[iC-3] if iC-3 >= 0 and iC-3 < len(v10) else ctx["vwap"][iC-1] if iC>0 else vcur
            slope = abs((vcur or px) - (vprev or px)) / max(1.0, px)
            slope_mx = p.get("VWAP_SLOPE_MAX", 0.00060)
            slope_ok = slope <= slope_mx

        # Basic volatility guard from profile
        atr_min = p.get("ATR_PCT_MIN", 0.0004)
        atr_max = p.get("ATR_PCT_MAX", 0.0200)
        atr_ok  = (atr_pct >= atr_min) and (atr_pct <= atr_max)

        # --- Route selection ---
        # 1) Breakout if we just escaped a range
        if adx_val is not None and adx_val <= self.adx_thr and (bk_up or bk_dn) and atr_ok:
            # delegate to breakout module
            return self.breakout.evaluate(ohlc, ctx)

        # 2) Established trend if ADX strong
        if adx_val is not None and adx_val > self.adx_thr:
            return self.trend.evaluate(ohlc, ctx)

        # 3) Otherwise range -> choose scalper (m1) or mean reversion (h1)
        if self._scalp_mode:
            # need flat-ish slope to mean revert safely
            if slope_ok and atr_ok:
                return self.scalper.evaluate(ohlc, ctx)
            # If slope too steep but ADX not high, better to WAIT than fight direction
            return Signal(type="WAIT", reason="Trend regime")
        else:
            if atr_ok:
                return self.revert.evaluate(ohlc, ctx)
            return Signal(type="WAIT", reason="ATR range")
