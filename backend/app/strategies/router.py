# backend/app/strategies/router.py
"""Adaptive router-as-a-strategy.

It detects market regime and delegates to the correct sub-strategy:
- Range (low ADX, flat slope): Level King — Regime (VWAP band scalper) or MeanReversion
- Breakout transition: Breakout (H1)
- Established trend: TrendFollow (H1)
"""

from typing import List, Dict, Any
from .base import Strategy, Signal
from ..ta import adx, atr, donchian, ema

from .level_king_regime import LevelKingRegime
from .mean_reversion import MeanReversion
from .breakout import Breakout
from .trend_follow import TrendFollow


def _aggregate(ohlc: List[Dict[str, Any]], step_sec: int) -> List[Dict[str, Any]]:
    """Aggregate 1m candles to multi-minute/hour candles."""
    if not ohlc:
        return []
    out: List[Dict[str, Any]] = []
    bucket = (ohlc[0]["time"] // step_sec) * step_sec
    cur: Dict[str, Any] | None = None
    for c in ohlc:
        b = (c["time"] // step_sec) * step_sec
        if b != bucket:
            if cur:
                out.append(cur)
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
    if cur:
        out.append(cur)
    return out


class StrategyRouter(Strategy):
    """ONE adaptive bot that chooses among sub-strategies automatically."""
    name = "Adaptive Router"

    def __init__(self):
        self.scalper = LevelKingRegime()
        self.revert = MeanReversion()
        self.breakout = Breakout()
        self.trend = TrendFollow()
        self.adx_thr = 25

    def pick(self, scalp_mode: bool) -> "StrategyRouter":
        self._scalp_mode = bool(scalp_mode)
        return self

    def evaluate(self, ohlc: List[Dict[str, Any]], ctx: Dict[str, Any]) -> Signal:
        # Ensure we have both series in context
        m1 = ctx.get("m1") or ohlc
        h1 = ctx.get("h1") or ohlc

        # Select the index that corresponds to the series 'ohlc' passed in by engine
        iC = ctx.get("iC")
        if iC is None or iC < ctx.get("min_bars", 0):
            return Signal(type="WAIT", reason="Loading...")

        p = ctx.get("profile", {}) or {}
        px = ohlc[iC]["close"]

        # --- Regime features on the active TF (m1 if scalp, else h1) ---
        if self._scalp_mode:
            m5 = _aggregate(m1, 300)
            a = adx(m5, 14)
            adx_val = a[-2] if len(a) >= 2 else None
            A = atr(m1, 14)
            atr_pct = (A[ctx.get("iC_m1")] or 0.0) / max(1.0, px) if ctx.get("iC_m1") is not None else 0.0
            dc = donchian(m1, 20)
            hi_prev = dc["hi"][ctx["iC_m1"] - 1] if ctx.get("iC_m1", 0) > 0 else None
            lo_prev = dc["lo"][ctx["iC_m1"] - 1] if ctx.get("iC_m1", 0) > 0 else None
        else:
            a = adx(h1, 14)
            adx_val = a[-2] if len(a) >= 2 else None
            A = atr(h1, 14)
            atr_pct = (A[ctx.get("iC_h1")] or 0.0) / max(1.0, px) if ctx.get("iC_h1") is not None else 0.0
            dc = donchian(h1, 20)
            hi_prev = dc["hi"][ctx["iC_h1"] - 1] if ctx.get("iC_h1", 0) > 0 else None
            lo_prev = dc["lo"][ctx["iC_h1"] - 1] if ctx.get("iC_h1", 0) > 0 else None

        bk_up = bool(hi_prev and px > hi_prev)
        bk_dn = bool(lo_prev and px < lo_prev)

        # VWAP slope gate (skip mean-revert if slope too steep)
        slope_ok = True
        if ctx.get("vwap") and ctx.get("iC_m1") is not None:
            v10 = ema(ctx["vwap"], 10)
            vcur = v10[ctx["iC_m1"]] if ctx["iC_m1"] < len(v10) else ctx["vwap"][ctx["iC_m1"]]
            vprev = v10[ctx["iC_m1"] - 3] if (ctx["iC_m1"] - 3) >= 0 and (ctx["iC_m1"] - 3) < len(v10) else (ctx["vwap"][ctx["iC_m1"] - 1] if ctx["iC_m1"] > 0 else vcur)
            slope = abs((vcur or px) - (vprev or px)) / max(1.0, px)
            slope_mx = p.get("VWAP_SLOPE_MAX", 0.00060)
            slope_ok = slope <= slope_mx

        # Basic volatility guard
        atr_min = p.get("ATR_PCT_MIN", 0.0004)
        atr_max = p.get("ATR_PCT_MAX", 0.0200)
        atr_ok = (atr_pct >= atr_min) and (atr_pct <= atr_max)

        # --- Route selection ---
        if adx_val is not None and adx_val <= self.adx_thr and (bk_up or bk_dn) and atr_ok:
            # Breakout on H1
            return self.breakout.evaluate(h1, ctx)

        if adx_val is not None and adx_val > self.adx_thr:
            # Established trend on H1
            return self.trend.evaluate(h1, ctx)

        # Otherwise range: scalper on m1 or mean-reversion on h1
        if self._scalp_mode:
            if slope_ok and atr_ok:
                return self.scalper.evaluate(m1, ctx)
            return Signal(type="WAIT", reason="Trend regime")
        else:
            if atr_ok:
                return self.revert.evaluate(h1, ctx)
            return Signal(type="WAIT", reason="ATR range")
