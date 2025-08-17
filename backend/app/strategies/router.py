# backend/app/strategies/router.py
"""Adaptive router-as-a-strategy (the ONE "Ultimate Bot").

- Detects regime using ADX, ATR, Donchian, VWAP slope
- Delegates to the correct sub-strategy:
    - Range (low ADX, flat slope): Level King — Regime (VWAP scalper) or MeanReversion
    - Breakout transition: Breakout (on H1)
    - Established trend: TrendFollow (on H1)
"""

from .base import Strategy, Signal
from ..ta import adx, atr, donchian, ema
from typing import List, Dict

from .level_king_regime import LevelKingRegime
from .mean_reversion import MeanReversion
from .breakout import Breakout
from .trend_follow import TrendFollow


def _aggregate(ohlc: List[Dict], step_sec: int) -> List[Dict]:
    if not ohlc:
        return []
    out: List[Dict] = []
    bucket = (ohlc[0]["time"] // step_sec) * step_sec
    cur: Dict | None = None
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
    name = "Adaptive Router"

    def __init__(self):
        self.scalper  = LevelKingRegime()
        self.revert   = MeanReversion()
        self.breakout = Breakout()
        self.trend    = TrendFollow()
        self.adx_thr  = 25  # regime split on ADX

    def pick(self, scalp_mode: bool) -> "StrategyRouter":
        self._scalp_mode = bool(scalp_mode)
        return self

    def evaluate(self, ohlc: List[Dict], ctx: Dict) -> Signal:
        iC = ctx.get("iC")
        if iC is None or iC < ctx.get("min_bars", 5):
            return Signal(type="WAIT", reason="Loading...")

        px = ohlc[iC]["close"]
        A  = atr(ohlc, 14)
        atr_pct = (A[iC] or 0.0) / max(1.0, px)

        # Donchian on current TF for quick regime hints
        dc = donchian(ohlc, 20)
        hi_prev = dc["hi"][iC - 1] if iC > 0 else None
        lo_prev = dc["lo"][iC - 1] if iC > 0 else None
        bk_up = bool(hi_prev is not None and px > hi_prev)
        bk_dn = bool(lo_prev is not None and px < lo_prev)

        # ADX: use 5m aggregation when scalping, otherwise current TF
        if getattr(self, "_scalp_mode", True):
            m5 = _aggregate(ohlc, 300)
            a = adx(m5, 14)
            adx_val = a[-2] if len(a) >= 2 else None
        else:
            a = adx(ohlc, 14)
            adx_val = a[-2] if len(a) >= 2 else None

        # VWAP slope gate if provided in ctx
        slope_ok = True
        p = ctx.get("profile", {}) or {}
        if ctx.get("vwap"):
            v10 = ema(ctx["vwap"], 10)
            vcur = v10[iC] if iC < len(v10) else ctx["vwap"][iC]
            vprev = v10[iC - 3] if iC - 3 >= 0 and iC - 3 < len(v10) else (ctx["vwap"][iC - 1] if iC > 0 else vcur)
            slope = abs((vcur or px) - (vprev or px)) / max(1.0, px)
            slope_mx = p.get("VWAP_SLOPE_MAX", 0.00060)
            slope_ok = slope <= slope_mx

        atr_min = p.get("ATR_PCT_MIN", 0.0004)
        atr_max = p.get("ATR_PCT_MAX", 0.0200)
        atr_ok  = (atr_pct >= atr_min) and (atr_pct <= atr_max)

        # 1) Breakout when low-ADX range just breaks
        if (adx_val is not None) and (adx_val <= self.adx_thr) and (bk_up or bk_dn) and atr_ok:
            # evaluate on H1 view for robustness
            h1 = _aggregate(ohlc, 3600) if getattr(self, "_scalp_mode", True) else ohlc
            if len(h1) >= 2:
                ctx2 = dict(ctx)
                ctx2["iC"] = len(h1) - 2
                ctx2["min_bars"] = max(120, ctx2.get("min_bars", 120))
                return self.breakout.evaluate(h1, ctx2)

        # 2) Established trend when ADX strong
        if (adx_val is not None) and (adx_val > self.adx_thr):
            h1 = _aggregate(ohlc, 3600) if getattr(self, "_scalp_mode", True) else ohlc
            if len(h1) >= 2:
                ctx2 = dict(ctx)
                ctx2["iC"] = len(h1) - 2
                ctx2["min_bars"] = max(220, ctx2.get("min_bars", 220))
                return self.trend.evaluate(h1, ctx2)

        # 3) Otherwise: range mode
        if getattr(self, "_scalp_mode", True):
            # scalper needs flat-ish slope
            if slope_ok and atr_ok:
                return self.scalper.evaluate(ohlc, ctx)
            return Signal(type="WAIT", reason="Trend regime")
        else:
            if atr_ok:
                return self.revert.evaluate(ohlc, ctx)
            return Signal(type="WAIT", reason="ATR range")
