"""Level King — Profiled (m1 scalper; VWAP mean‑reversion) for Strategy V2.

Profile gates implemented:
- ATR% band (profile-specific)
- VWAP EMA10 slope ≤ cap (over ~3 bars)
- Spread ≤ 10 bps (only enforced when feed spread looks realistic)
- Fee/TP constraint
- Top‑down bias:
  * LIGHT: if H1 ADX ≤ 18 → both sides; else align with H1 EMA200 bias
  * HEAVY: always align with H1 EMA200 bias
- Volume confirmation on reclaim candle:
  * If feed volume is "tick-ish" (1 Hz polling → ~60 per bar), bypass this gate.
  * Otherwise: m1 vol ≥ mult × median(last 20)
  * Warm-start exception: when seeding with real exchange volumes, allow the
    gate to pass until the tick-volume window fills in.
- Candlestick quality filters (engulfing/hammer/shooting star)
- Entry = overshoot (prev bar) + reclaim (current bar green/red)
- band_pct = max(0.20%, 0.7 * ATR%)
- tp_pct   = max(0.20%, 0.8 * band_pct)  (used for stop_dist & take_dist)
"""

from statistics import median
from .base import Strategy, Signal
from ..ta import ema, atr, adx


def _body(c): return abs(c["close"] - c["open"])
def _range(c): return max(1e-9, c["high"] - c["low"])
def _is_green(c): return c["close"] >= c["open"]
def _is_red(c): return c["close"] <= c["open"]

def _bull_engulf(prev, cur):
    return _is_green(cur) and (prev["close"] < prev["open"]) and (cur["close"] > prev["open"]) and (cur["open"] < prev["close"])

def _bear_engulf(prev, cur):
    return _is_red(cur) and (prev["close"] > prev["open"]) and (cur["close"] < prev["open"]) and (cur["open"] > prev["close"])

def _hammer(cur, heavy=False):
    rng = _range(cur)
    body = _body(cur)
    lower = cur["low"]
    lo_wick = (cur["open"] if cur["open"] < cur["close"] else cur["close"]) - lower
    cond = lo_wick >= body  # lower wick at least body
    if heavy:
        # close in top 25% of range
        close_pos = (cur["close"] - lower) / max(1e-8, rng)
        cond = cond and (close_pos >= 0.75)
    return cond

def _shooting_star(cur, heavy=False):
    rng = _range(cur)
    body = _body(cur)
    lower = cur["low"]
    upper = cur["high"]
    hi_wick = upper - (cur["open"] if cur["open"] > cur["close"] else cur["close"])
    cond = hi_wick >= body  # upper wick at least body
    if heavy:
        # close in bottom 25% of range
        close_pos = (cur["close"] - lower) / max(1e-8, rng)
        cond = cond and (close_pos <= 0.25)
    return cond

def _looks_like_tick_volume(vols: list[float]) -> bool:
    """Heuristic: 1 Hz polling → ~60 'volume' per minute with modest variation."""
    if not vols or len(vols) < 10:
        return False
    vmin = min(vols)
    vmax = max(vols)
    med = median(vols)
    if med <= 0:
        return False
    variation = (vmax - vmin) / max(1.0, med)
    # Treat ~30–120 ticks/min with up to ~60% swing as "tick-ish".
    return (30 <= med <= 120) and (variation <= 0.60)


class LevelKingRegime(Strategy):
    """Profile-aware scalper around VWAP bands."""
    name = "Level King — Profiled"

    def evaluate(self, m1: list[dict], ctx: dict) -> Signal:
        iC = ctx.get("iC")
        min_bars = int(ctx.get("min_bars", 0))
        if iC is None or len(m1) < min_bars:
            return Signal(type="WAIT", reason="Need short warmup")
        if not ctx.get("daily_ok", True):
            return Signal(type="WAIT", reason="Daily cap")
        if not ctx.get("cooldown_ok", True):
            return Signal(type="WAIT", reason="Pause after recent trade")
        if ctx.get("fills", 0) >= ctx.get("max_fills", 60):
            return Signal(type="WAIT", reason="Fill cap")

        px = m1[iC]["close"]
        a14 = atr(m1, 14)
        vwap_series = ctx.get("vwap") or []
        vwap = vwap_series[iC] if len(vwap_series) > iC else None
        if vwap is None:
            return Signal(type="WAIT", reason="VWAP warmup")

        prof = ctx.get("profile", {})
        atr_min = prof.get("ATR_PCT_MIN", 0.0004)
        atr_max = prof.get("ATR_PCT_MAX", 0.02)
        atr_pct = (a14[iC] or 0.0) / max(1.0, px)

        # Startup ATR bypass — shrink to 5 bars max
        if len(m1) >= 6 and (atr_pct < atr_min or atr_pct > atr_max):
            return Signal(type="WAIT", reason="ATR range")

        # Slope gate to avoid steep trends
        v10 = ema([x if x is not None else vwap for x in vwap_series], 10)
        prev_ix = iC - 3 if iC >= 3 else (iC - 1 if iC > 0 else iC)
        slope = abs((v10[iC] or vwap) - (v10[prev_ix] or vwap)) / max(1.0, px)
        slope_mx = prof.get("VWAP_SLOPE_MAX", 0.00060)
        if slope > slope_mx:
            return Signal(type="WAIT", reason="Trend regime")

        # Spread cap — only enforce if the feed spread looks realistic.
        bid, ask = ctx.get("bid"), ctx.get("ask")
        if bid and ask:
            mid = (bid + ask) / 2.0
            spread_bps = ((ask - bid) / max(1e-12, mid)) * 10000.0
            # Ignore absurd retail endpoints (e.g., padded >200 bps) instead of blocking.
            if spread_bps <= 200:
                if spread_bps > prof.get("SPREAD_BPS_MAX", 10):
                    return Signal(type="WAIT", reason="Spread too wide")

        tp_floor = prof.get("TP_FLOOR", 0.0020)
        band_pct = max(tp_floor, 0.7 * atr_pct)
        tp_pct = max(tp_floor, 0.8 * band_pct)
        fee_r = (2 * ctx.get("fee_rate", 0.0001)) / tp_pct
        if fee_r > prof.get("FEE_R_MAX", 0.25):
            return Signal(type="WAIT", reason="Fees>limitR")

        # Volume confirmation on reclaim candle
        win = m1[max(0, iC - 20): iC]  # last 20 closed bars
        vols = [c.get("volume", 0.0) for c in win if isinstance(c.get("volume", 0.0), (int, float))]
        med = median(vols) if vols else 0.0
        cur_vol = m1[iC].get("volume", 0.0)

        # Bypass when we detect tick-volume *or* we're in the cold-start where seed volumes dwarf tick counts.
        tickish = _looks_like_tick_volume(vols)
        if tickish or (med >= 500 and cur_vol <= 120 and len(win) >= 10):
            vol_ok = True
        else:
            vol_mult = float(prof.get("SCALP_VOL_MULT", 1.5))
            vol_ok = (cur_vol >= (vol_mult * med)) if med > 0 else True

        # Candlestick quality
        prev = m1[iC - 1] if iC > 0 else None
        cur = m1[iC]
        heavy = (ctx.get("profile_mode_active") == "HEAVY")
        long_ok_pat = (_bull_engulf(prev, cur) if prev else False) or _hammer(cur, heavy=heavy)
        short_ok_pat = (_bear_engulf(prev, cur) if prev else False) or _shooting_star(cur, heavy=heavy)

        # Top-down bias using H1 info
        h1 = ctx.get("h1") or []
        iH = ctx.get("iC_h1")
        bias_ok_long = True
        bias_ok_short = True
        if h1 and iH is not None and iH >= 0:
            closes = [c["close"] for c in h1]
            e200 = ema(closes, 200)
            ax = adx(h1, 14)
            h1_adx = ax[iH] or 0.0
            ema_bias_up = (e200[iH] is not None) and (h1[iH]["close"] >= (e200[iH] or 0.0))
            ema_bias_dn = (e200[iH] is not None) and (h1[iH]["close"] <= (e200[iH] or 0.0))

            if ctx.get("profile_mode_active") == "HEAVY":
                # HEAVY: always align with EMA200 bias
                bias_ok_long = bool(ema_bias_up)
                bias_ok_short = bool(ema_bias_dn)
            else:
                # LIGHT: if H1 ADX ≤ 18 allow both sides; else align with EMA200
                if h1_adx <= 18:
                    bias_ok_long = True
                    bias_ok_short = True
                else:
                    bias_ok_long = bool(ema_bias_up)
                    bias_ok_short = bool(ema_bias_dn)

        # Overshoot + reclaim logic
        vprev = vwap_series[iC - 1] if iC > 0 else None
        overshoot_long = bool(prev and vprev and prev["low"] <= vprev * (1 - band_pct * 1.05))
        reclaim_long = bool(px >= (vwap or px) * (1 - band_pct * 0.70) and _is_green(cur))
        overshoot_short = bool(prev and vprev and prev["high"] >= vprev * (1 + band_pct * 1.05))
        reclaim_short = bool(px <= (vwap or px) * (1 + band_pct * 0.70) and _is_red(cur))

        # Long
        if overshoot_long and reclaim_long and vol_ok and long_ok_pat and bias_ok_long:
            return Signal(type="BUY", reason="Band reclaim long", stop_dist=px * tp_pct, take_dist=px * tp_pct, score=4)
        # Short
        if overshoot_short and reclaim_short and vol_ok and short_ok_pat and bias_ok_short:
            return Signal(type="SELL", reason="Band reclaim short", stop_dist=px * tp_pct, take_dist=px * tp_pct, score=4)

        return Signal(type="WAIT", reason="Inside bands")
