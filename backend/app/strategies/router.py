# backend/app/strategies/router.py
"""Adaptive router-as-a-strategy with hysteresis and status telemetry.

It detects market regime and delegates to the correct sub-strategy:
- Range (low ADX, flat slope): Level King — Regime (m1 scalper) or MeanReversion (H1)
- Breakout transition: Breakout (H1)
- Established trend: TrendFollow (H1)

Adds:
- ADX hysteresis: enter trend at adx_on, exit at adx_off to avoid flip-flops
- last_regime / last_bias / last_adx / last_atr_pct / last_strategy for UI/telemetry
"""

from typing import List, Dict, Any, Optional
from .base import Strategy, Signal
from ..ta import adx, atr, donchian, ema


def _aggregate(ohlc: List[Dict[str, Any]], step_sec: int) -> List[Dict[str, Any]]:
    """Aggregate candles (e.g., 1m → 5m)."""
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

        # ADX thresholds
        self.adx_thr = 25      # general pivot (literature common)
        self.adx_on = 27       # enter "trend" regime if >= this
        self.adx_off = 23      # leave "trend" regime if <= this

        # Internal state
        self._scalp_mode = True
        self._mode = "range"   # "range" | "trend" | "breakout"

        # Telemetry for UI
        self.last_regime: Optional[str] = None
        self.last_bias: Optional[str] = None    # "Bullish" | "Bearish" | None
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

        px = ohlc[iC]["close"]
        p = ctx.get("profile", {}) or {}

        # --- Regime features on active TF (m1 if scalp, else h1) ---
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

        # VWAP slope gate for scalping (skip range trades during steep slope)
        slope_ok = True
        if ctx.get("vwap") and ctx.get("iC_m1") is not None:
            v10 = ema(ctx["vwap"], 10)
            i_m1 = ctx["iC_m1"]
            vcur = v10[i_m1] if i_m1 < len(v10) else ctx["vwap"][i_m1]
            vprev = v10[i_m1 - 3] if (i_m1 - 3) >= 0 and (i_m1 - 3) < len(v10) else (
                ctx["vwap"][i_m1 - 1] if i_m1 > 0 else vcur
            )
            slope = abs((vcur or px) - (vprev or px)) / max(1.0, px)
            slope_mx = p.get("VWAP_SLOPE_MAX", 0.00060)
            slope_ok = slope <= slope_mx

        # Volatility guard
        atr_min = p.get("ATR_PCT_MIN", 0.0004)
        atr_max = p.get("ATR_PCT_MAX", 0.0200)
        atr_ok = (atr_pct >= atr_min) and (atr_pct <= atr_max)

        # Donchian break flags (explicit None checks to avoid truthiness traps)
        bk_up = (hi_prev is not None) and (px > hi_prev)
        bk_dn = (lo_prev is not None) and (px < lo_prev)

        # --- Hysteresis regime logic ---
        mode = self._mode
        if adx_val is not None:
            if mode in ("trend", "breakout") and adx_val <= self.adx_off:
                mode = "range"
            elif mode == "range" and adx_val >= self.adx_on:
                mode = "trend"

        # Breakout takes precedence when ADX is low and we see a break
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
            if slope_ok and atr_ok:
                self.last_strategy = self.scalper.name
                return self.scalper.evaluate(m1, ctx)
            self.last_strategy = self.scalper.name
            return Signal(type="WAIT", reason="Trend regime")
        else:
            if atr_ok:
                self.last_strategy = self.revert.name
                return self.revert.evaluate(h1, ctx)
            self.last_strategy = self.revert.name
            return Signal(type="WAIT", reason="ATR range")
