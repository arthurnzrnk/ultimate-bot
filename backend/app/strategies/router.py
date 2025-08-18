# backend/app/strategies/router.py
"""Adaptive router-as-a-strategy with hysteresis and status telemetry.

Regimes:
- Range (low ADX): Level King — Profiled (m1) or Mean-Reversion (H1) by gates
- Breakout (ADX ≤ 25 + Donchian break + ATR% in band): Breakout (H1)
- Trend (ADX ≥ 27, exit ≤ 23): TrendFollow (H1)

Telemetry: last_regime / last_bias / last_adx / last_atr_pct / last_strategy
"""

from typing import List, Dict, Any, Optional
from .base import Strategy, Signal
from ..ta import adx, atr, donchian, ema


def _aggregate(ohlc: List[Dict[str, Any]], step_sec: int) -> List[Dict[str, Any]]:
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
        from .level_king_regime import LevelKingRegime
        from .mean_reversion import MeanReversion
        from .breakout import Breakout
        from .trend_follow import TrendFollow

        self.scalper = LevelKingRegime()
        self.revert = MeanReversion()
        self.breakout = Breakout()
        self.trend = TrendFollow()

        # ADX thresholds with hysteresis
        self.adx_thr = 25      # breakout threshold
        self.adx_on = 27       # enter trend
        self.adx_off = 23      # exit trend

        # Internal state
        self._scalp_mode = True
        self._mode = "range"   # "range" | "trend" | "breakout"

        # Telemetry for UI
        self.last_regime: Optional[str] = None
        self.last_bias: Optional[str] = None
        self.last_adx: Optional[float] = None
        self.last_atr_pct: Optional[float] = None
        self.last_strategy: Optional[str] = None

    def pick(self, scalp_mode: bool) -> "StrategyRouter":
        self._scalp_mode = bool(scalp_mode)
        return self

    def _calc_bias(self, h1: List[Dict[str, Any]], i: Optional[int]) -> Optional[str]:
        if i is None or i < 0 or i >= len(h1):
            return None
        closes = [c["close"] for c in h1]
        e200 = ema(closes, 200)
        if e200[i] is None:
            return None
        return "Bullish" if h1[i]["close"] >= (e200[i] or 0.0) else "Bearish"

    def evaluate(self, ohlc: List[Dict[str, Any]], ctx: Dict[str, Any]) -> Signal:
        # Ensure we have both series in context
        m1 = ctx.get("m1") or ohlc
        h1 = ctx.get("h1") or ohlc

        iC = ctx.get("iC")
        if iC is None or iC < ctx.get("min_bars", 0):
            return Signal(type="WAIT", reason="Loading...")

        # --- Regime features ---
        if self._scalp_mode:
            m5 = _aggregate(m1, 300)
            a = adx(m5, 14)
            adx_val = a[-2] if len(a) >= 2 else None
            A = atr(m1, 14)
            i_m1 = ctx.get("iC_m1")
            atr_pct = ((A[i_m1] or 0.0) / max(1.0, m1[i_m1]["close"])) if i_m1 is not None else 0.0
            dc = donchian(m1, 20)
            hi_prev = dc["hi"][i_m1 - 1] if (i_m1 is not None and i_m1 > 0) else None
            lo_prev = dc["lo"][i_m1 - 1] if (i_m1 is not None and i_m1 > 0) else None
        else:
            a = adx(h1, 14)
            adx_val = a[-2] if len(a) >= 2 else None
            A = atr(h1, 14)
            i_h1 = ctx.get("iC_h1")
            atr_pct = ((A[i_h1] or 0.0) / max(1.0, h1[i_h1]["close"])) if i_h1 is not None else 0.0
            dc = donchian(h1, 20)
            hi_prev = dc["hi"][i_h1 - 1] if (i_h1 is not None and i_h1 > 0) else None
            lo_prev = dc["lo"][i_h1 - 1] if (i_h1 is not None and i_h1 > 0) else None

        # Telemetry
        self.last_adx = adx_val
        self.last_atr_pct = atr_pct
        self.last_bias = self._calc_bias(h1, ctx.get("iC_h1"))

        # ATR% band guard (uses profile band via strategies too; this is coarse)
        atr_min = (ctx.get("profile") or {}).get("ATR_PCT_MIN", 0.0004)
        atr_max = (ctx.get("profile") or {}).get("ATR_PCT_MAX", 0.0200)
        atr_ok = (atr_pct >= atr_min) and (atr_pct <= atr_max)

        # Donchian break flags
        bk_up = (hi_prev is not None) and ( (m1 if self._scalp_mode else h1)[ctx.get("iC_m1") if self._scalp_mode else ctx.get("iC_h1")]["close"] > hi_prev )
        bk_dn = (lo_prev is not None) and ( (m1 if self._scalp_mode else h1)[ctx.get("iC_m1") if self._scalp_mode else ctx.get("iC_h1")]["close"] < lo_prev )

        # Hysteresis regime logic
        mode = self._mode
        if adx_val is not None:
            if mode in ("trend", "breakout") and adx_val <= self.adx_off:
                mode = "range"
            elif mode == "range" and adx_val >= self.adx_on:
                mode = "trend"

        # Breakout priority when ADX low and Donchian break occurs and ATR% within band
        if adx_val is not None and adx_val <= self.adx_thr and (bk_up or bk_dn) and atr_ok:
            mode = "breakout"

        self._mode = mode
        self.last_regime = {"range": "Range", "trend": "Trending", "breakout": "Breakout"}.get(mode, "Unknown")

        # --- Route selection ---
        if mode == "breakout":
            self.last_strategy = self.breakout.name
            return self.breakout.evaluate(h1, ctx)

        if mode == "trend":
            self.last_strategy = self.trend.name
            return self.trend.evaluate(h1, ctx)

        # Range:
        if self._scalp_mode:
            self.last_strategy = self.scalper.name
            if atr_ok:
                return self.scalper.evaluate(m1, ctx)
            return Signal(type="WAIT", reason="ATR range")
        else:
            self.last_strategy = self.revert.name
            if atr_ok:
                return self.revert.evaluate(h1, ctx)
            return Signal(type="WAIT", reason="ATR range")
