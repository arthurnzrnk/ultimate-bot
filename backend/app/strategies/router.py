"""Strategy router.

The router encapsulates the logic for selecting which strategy to use
depending on the current trading mode. In our design, the user toggles
between scalping (1m) and trend following (1h) modes, so the router simply
returns the appropriate strategy instance. To add more complex regime
detection or multi‑strategy allocation, extend this class to incorporate
additional selection logic.
"""

from dataclasses import dataclass
from .base import Strategy, Signal
from .level_king_regime import LevelKingRegime
from .trend_follow import TrendFollow
from .breakout import Breakout
from ..ta import adx, atr, ema, donchian

# Helpers for building candles and VWAP on the fly
def aggregate_to(candles, step_sec: int):
    if not candles: return []
    out = []
    cur = None
    bucket = (candles[0]["time"] // step_sec) * step_sec
    for c in candles:
        b = (c["time"] // step_sec) * step_sec
        if b != bucket:
            if cur: out.append(cur)
            bucket = b; cur = None
        if cur is None:
            cur = {
                "time": b,
                "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
                "volume": c.get("volume", 0.0)
            }
        else:
            cur["high"] = max(cur["high"], c["high"])
            cur["low"]  = min(cur["low"],  c["low"])
            cur["close"] = c["close"]
            cur["volume"] += c.get("volume", 0.0)
    if cur: out.append(cur)
    return out

def session_vwap(m1):
    out = [None] * len(m1)
    day = None; pv = 0.0; vv = 0.0
    for i, c in enumerate(m1):
        d = str(__import__("datetime").datetime.utcfromtimestamp(c["time"]).date())
        if d != day:
            day = d; pv = 0.0; vv = 0.0
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        v  = max(1e-8, c.get("volume", 0.0))
        pv += tp * v; vv += v
        out[i] = pv / max(1e-8, vv)
    return out

@dataclass
class Regime:
    name: str
    reason: str

class RouterStrategy(Strategy):
    """
    The 'Ultimate Bot': detects regime on H1 and activates exactly one module:
      - RANGE  -> LevelKingRegime (VWAP band reclaims)
      - BREAKOUT -> Breakout (volatility squeeze→expansion)
      - TREND -> TrendFollow
    Includes hysteresis to avoid whipsawing between modes.
    """
    name = "Ultimate Bot (Adaptive)"

    def __init__(self):
        self.mr = LevelKingRegime()
        self.bo = Breakout()
        self.tf = TrendFollow()
        self._last_regime = None

    def _detect_regime(self, h1, cfg, idx):
        if idx is None or idx < cfg.get("min_bars", 220):
            return Regime("WARMUP", "Need H1 history")
        closes = [c["close"] for c in h1]
        ema200 = ema(closes, 200)
        a14    = atr(h1, 14)
        ax     = adx(h1, 14)
        dc     = donchian(h1, cfg.get("donchian_len", 20))
        px     = h1[idx]["close"]

        adx_val = ax[idx] or 0.0
        atr_pct = (a14[idx] or 0.0) / max(1.0, px)
        ema_up  = ema200[idx] and ema200[idx] > ema200[max(0, idx-5)]
        ema_dn  = ema200[idx] and ema200[idx] < ema200[max(0, idx-5)]
        hi_prev = dc["hi"][max(0, idx-1)]
        lo_prev = dc["lo"][max(0, idx-1)]
        above = (px > hi_prev) if hi_prev is not None else False
        below = (px < lo_prev) if lo_prev is not None else False

        # thresholds
        adx_trend_min = cfg.get("adx_trend_min", 25)
        atr_lo        = cfg.get("atr_lo_pct", 0.0006)
        atr_hi        = cfg.get("atr_hi_pct", 0.02)

        if adx_val >= adx_trend_min and (ema_up or ema_dn):
            return Regime("TREND", f"ADX {adx_val:.1f} with EMA bias")
        if atr_pct <= atr_lo and (above or below):
            return Regime("BREAKOUT", "Low vol squeeze + channel break")
        if adx_val < adx_trend_min and atr_pct <= atr_hi:
            return Regime("RANGE", "Low trend strength; inside range")
        return Regime("RANGE", "Default to range")

    def evaluate(self, m1: list[dict], ctx: dict) -> Signal:
        # Build H1 if not provided
        h1 = ctx.get("h1") or aggregate_to(m1, 3600)
        iC_m1 = len(m1) - 2 if len(m1) >= 2 else None
        iC_h1 = len(h1) - 2 if len(h1) >= 2 else None

        cfg = {
            "min_bars": ctx.get("min_h1_bars", 220),
            "donchian_len": ctx.get("donchian_len", 20),
            "adx_trend_min": ctx.get("adx_trend_min", 25),
            "atr_lo_pct": ctx.get("atr_lo_pct", 0.0006),
            "atr_hi_pct": ctx.get("atr_hi_pct", 0.02),
        }
        regime = self._detect_regime(h1, cfg, iC_h1)
        # hysteresis (simple): switch only if regime appears twice in a row
        if self._last_regime and regime.name != "WARMUP":
            prev = self._detect_regime(h1[:-1], cfg, (iC_h1 - 1) if iC_h1 else None)
            if prev.name != regime.name:
                regime = Regime(self._last_regime, f"Hold {self._last_regime} (hysteresis)")
        self._last_regime = regime.name

        # Dispatch
        if regime.name == "RANGE":
            vwap = session_vwap(m1)
            mr_ctx = dict(ctx)
            mr_ctx.update({
                "iC": iC_m1,
                "vwap": vwap,
                "profile": {
                    "ATR_PCT_MIN": ctx.get("mr_atr_min", 0.0004),
                    "ATR_PCT_MAX": ctx.get("mr_atr_max", 0.02),
                    "VWAP_SLOPE_MAX": ctx.get("mr_vwap_slope_max", 0.00060),
                    "SPREAD_BPS_MAX": ctx.get("mr_spread_bps_max", 10),
                    "TP_FLOOR": ctx.get("mr_tp_floor", 0.0020),
                    "FEE_R_MAX": ctx.get("fee_r_max", 0.25),
                },
                "max_fills": ctx.get("max_fills", 60),
            })
            sig = self.mr.evaluate(m1, mr_ctx)
        elif regime.name == "BREAKOUT":
            bo_ctx = dict(ctx)
            bo_ctx.update({"iC_h1": iC_h1})
            sig = self.bo.evaluate(h1, bo_ctx)
        elif regime.name == "TREND":
            tf_ctx = dict(ctx)
            tf_ctx.update({
                "iC_h1": iC_h1,
                "trend_stop_atr": ctx.get("trend_stop_atr", 2.0),
                "trend_take_atr": ctx.get("trend_take_atr", 1.5),
            })
            sig = self.tf.evaluate(h1, tf_ctx)
        else:
            return Signal(type="WAIT", reason="Warmup")

        # Attach metadata for UI status
        meta = dict(sig.meta or {})
        meta.update({
            "regime": regime.name,
            "regime_reason": regime.reason,
            "module": {
                "RANGE": self.mr.name,
                "BREAKOUT": self.bo.name,
                "TREND": self.tf.name,
            }.get(regime.name, "—"),
        })
        sig.meta = meta
        return sig
