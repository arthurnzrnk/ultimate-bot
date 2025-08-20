# backend/app/strategies/router.py
"""Adaptive router-as-a-strategy with hysteresis and status telemetry.

Regimes:
- Range (low ADX): Level King — Profiled (m1) or Mean-Reversion (H1) by gates
- Breakout (H1): ADX ≤ 25 on H1 + Donchian break on H1 + ATR% in band
- Trend (H1): ADX ≥ 27 on H1, exit ≤ 23

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

        # ADX thresholds with hysteresis (on H1)
        self.adx_thr = 25      # breakout threshold (H1)
        self.adx_on = 27       # enter trend (H1)
        self.adx_off = 23      # exit trend (H1)

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
        min_bars = int(ctx.get("min_bars", 0))
        m1_len = len(m1)
        if iC is None or m1_len < min_bars:
            return Signal(type="WAIT", reason="Need short warmup")

        # --- Regime features ---
        # We *always* use H1 ADX for regime determination (trend/breakout),
        # even when in scalper mode, so router and H1 strategies are aligned.
        a_h1 = adx(h1, 14)
        adx_h1 = a_h1[-2] if len(a_h1) >= 2 else None

        if self._scalp_mode:
            m5 = _aggregate(m1, 300)
            a_m5 = adx(m5, 14)
            # Expose an m1-based ATR% for telemetry/guards (uses m1)
            A_m1 = atr(m1, 14)
            idx_m1 = ctx.get("iC_m1")
            atr_pct = ((A_m1[idx_m1] or 0.0) / max(1.0, m1[idx_m1]["close"])) if isinstance(idx_m1, int) and idx_m1 >= 0 else 0.0
        else:
            A_h1 = atr(h1, 14)
            idx_h1 = ctx.get("iC_h1")
            atr_pct = ((A_h1[idx_h1] or 0.0) / max(1.0, h1[idx_h1]["close"])) if isinstance(idx_h1, int) and idx_h1 >= 0 else 0.0

        # Telemetry
        self.last_adx = adx_h1
        self.last_atr_pct = atr_pct
        self.last_bias = self._calc_bias(h1, ctx.get("iC_h1"))

        # ATR% band guard (coarse; exact gates live in strategies)
        profile = ctx.get("profile") or {}
        atr_min = profile.get("ATR_PCT_MIN", 0.0004)
        atr_max = profile.get("ATR_PCT_MAX", 0.0200)
        atr_ok = (atr_pct >= atr_min) and (atr_pct <= atr_max)

        # Donchian break flags on H1 (aligns with Breakout/Trend strategies)
        idx_h1 = ctx.get("iC_h1")
        dc_h1 = donchian(h1, 20)
        if isinstance(idx_h1, int) and idx_h1 > 0 and idx_h1 < len(h1):
            hi_prev = dc_h1["hi"][idx_h1 - 1]
            lo_prev = dc_h1["lo"][idx_h1 - 1]
            px_now = h1[idx_h1]["close"]
            bk_up = (hi_prev is not None) and (px_now > hi_prev)
            bk_dn = (lo_prev is not None) and (px_now < lo_prev)
        else:
            bk_up = bk_dn = False

        # Hysteresis regime logic (on H1 ADX)
        mode = self._mode
        if adx_h1 is not None:
            if mode in ("trend", "breakout") and adx_h1 <= self.adx_off:
                mode = "range"
            elif mode == "range" and adx_h1 >= self.adx_on:
                mode = "trend"

        # Breakout priority when H1 ADX low and H1 Donchian breaks within ATR band
        if adx_h1 is not None and adx_h1 <= self.adx_thr and (bk_up or bk_dn) and atr_ok:
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
