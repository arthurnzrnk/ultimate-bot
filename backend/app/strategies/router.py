"""Router + Strategies for Strategy V3 (Dynamic VS/PS).

Implements:
- Regime: Trend / Breakout / Range with hysteresis.
- Bias: h1 EMA200.
- m1 VWAP Mean-Reversion (Enhanced Level King) with scoring.
- h1 Mean-Reversion, Breakout, Trend-Following.

All thresholds smoothly adapt via VS/PS supplied in ctx.
"""

from statistics import median
from typing import Optional

from .base import Strategy, Signal
from ..ta import ema, atr, adx, donchian, rsi, macd_line_signal


def _macd_state(line, sig, i) -> str:
    if i is None or i <= 0 or i >= len(line) or line[i] is None or sig[i] is None:
        return "flat"
    prev = (line[i - 1] or 0.0) - (sig[i - 1] or 0.0)
    cur = (line[i] or 0.0) - (sig[i] or 0.0)
    if prev <= 0 < cur or prev >= 0 > cur:
        return "cross"
    if cur > 0:
        return "up"
    if cur < 0:
        return "down"
    return "flat"


# ---------------- m1 scalper ----------------

class M1Scalp(Strategy):
    name = "m1 VWAP MR"

    def evaluate(self, ctx: dict) -> Signal:
        m1 = ctx["m1"]; i = ctx["iC_m1"]
        if i is None or i < 2 or len(m1) < max(6, ctx.get("min_bars", 5)):
            return Signal(type="WAIT", reason="Warmup")
        px = m1[i]["close"]
        # Indicators
        a14 = atr(m1, 14)
        atr_pct = (a14[i] or 0.0) / max(1.0, px)
        tp_floor = 0.0015  # 0.15%
        VS = float(ctx["VS"])
        # ATR band scale
        atr_min = 0.0005
        atr_max = 0.0175
        atr_min *= VS
        atr_max *= VS
        if atr_pct < atr_min or atr_pct > atr_max:
            return Signal(type="WAIT", reason="ATR band")
        # VWAP slope cap
        vwap = ctx["vwap"]
        if vwap[i] is None:
            return Signal(type="WAIT", reason="VWAP warmup")
        v10 = ema([x if x is not None else vwap[i] for x in vwap], 10)
        base = i - 3 if i >= 3 else (i - 1 if i > 0 else i)
        slope = abs((v10[i] or vwap[i]) - (v10[base] or vwap[base])) / max(1.0, px)
        if slope > (0.0005 * VS):  # 0.050% × VS
            return Signal(type="WAIT", reason="Slope cap")
        # Spread gate (if bid/ask present)
        bid, ask = ctx.get("bid"), ctx.get("ask")
        if bid and ask:
            mid = (bid + ask) / 2.0
            spread_bps = ((ask - bid) / max(1e-9, mid)) * 10000.0
            if spread_bps > 8.0:
                return Signal(type="WAIT", reason="Spread")
        # MTF alignment (EMA200 on h1), with CT exception
        h1 = ctx["h1"]; j = ctx["iC_h1"]
        closes_h1 = [c["close"] for c in h1]
        e200 = ema(closes_h1, 200)
        ema_bias_up = bool(e200[j] and h1[j]["close"] >= (e200[j] or 0.0)) if j is not None else True
        ema_bias_dn = bool(e200[j] and h1[j]["close"] <= (e200[j] or 0.0)) if j is not None else True
        ax_h1 = adx(h1, 14); adx_h1 = (ax_h1[j] or 0.0) if j is not None else 0.0
        rsi_m1 = rsi([c["close"] for c in m1], 14); rsi_now = rsi_m1[i] or 50.0
        allow_ct = (adx_h1 < 20.0 * VS) and ((rsi_now < 25.0) or (rsi_now > 75.0))

        # Volume quality on reclaim candle
        win = m1[max(0, i - 20):i]
        vols = [c.get("volume", 0.0) for c in win]
        vmed = median(vols) if vols else 0.0
        cur_vol = m1[i].get("volume", 0.0)
        vol_ok = (cur_vol >= (2.0 * vmed)) if vmed > 0 else True

        # Candle quality (engulfing preferred; hammer / shooting star allowed)
        prev = m1[i - 1]
        cur = m1[i]

        def _body(c): return abs(c["close"] - c["open"])
        def _range(c): return max(1e-9, c["high"] - c["low"])
        def _is_green(c): return c["close"] >= c["open"]
        def _is_red(c): return c["close"] <= c["open"]
        def bull_engulf(a, b):
            return _is_green(b) and (a["close"] < a["open"]) and (b["close"] > a["open"]) and (b["open"] < a["close"])
        def bear_engulf(a, b):
            return _is_red(b) and (a["close"] > a["open"]) and (b["close"] < a["open"]) and (b["open"] > a["close"])
        def hammer(b):
            rng = _range(b); body = _body(b)
            lower = min(b["open"], b["close"])
            lo_w = lower - b["low"]
            close_pos = (b["close"] - b["low"]) / max(1e-8, rng)
            return lo_w >= body and close_pos >= 0.75
        def shooting_star(b):
            rng = _range(b); body = _body(b)
            upper = max(b["open"], b["close"])
            hi_w = b["high"] - upper
            close_pos = (b["close"] - b["low"]) / max(1e-8, rng)
            return hi_w >= body and close_pos <= 0.25

        long_pat = bull_engulf(prev, cur) or hammer(cur)
        short_pat = bear_engulf(prev, cur) or shooting_star(cur)

        # Band math
        band_pct = max(0.0015, 0.75 * atr_pct)
        tp_pct = max(0.0015, 0.85 * band_pct)
        # Fee->TP constraint (maker 0.01%/side)
        fee_to_tp = (2 * 0.0001) / tp_pct
        if fee_to_tp > 0.20:
            return Signal(type="WAIT", reason="Fees")

        # Overshoot + reclaim
        vprev = ctx["vwap"][i - 1]
        long_ok_bias = ema_bias_up or allow_ct
        short_ok_bias = ema_bias_dn or allow_ct

        over_long = bool(vprev and (m1[i - 1]["low"] <= (vprev * (1 - 1.00 * band_pct))))
        reclaim_long = bool(cur["close"] >= (ctx["vwap"][i] * (1 - 0.65 * band_pct)) and _is_green(cur))
        over_short = bool(vprev and (m1[i - 1]["high"] >= (vprev * (1 + 1.00 * band_pct))))
        reclaim_short = bool(cur["close"] <= (ctx["vwap"][i] * (1 + 0.65 * band_pct)) and _is_red(cur))

        # Soft scoring
        closes_m1 = [c["close"] for c in m1]
        macd_l, macd_s = macd_line_signal(closes_m1, 12, 26, 9)
        macd_st = _macd_state(macd_l, macd_s, i)
        macd_align_long = macd_st in ("up", "cross")
        macd_align_short = macd_st in ("down", "cross")

        score_base = 4.0
        score_long = score_base
        score_short = score_base
        # rsi extreme
        if rsi_now is not None:
            if rsi_now < 30.0:
                score_long += 0.5
            if rsi_now > 70.0:
                score_short += 0.5
        # macd align
        if macd_align_long:
            score_long += 0.5
        if macd_align_short:
            score_short += 0.5
        # (cum delta optional) + h1 rsi extreme
        rsi_h1 = rsi([c["close"] for c in h1], 14); rsi_h1_now = rsi_h1[j] if j is not None else None
        if rsi_h1_now is not None and rsi_h1_now < 30.0:
            score_long += 0.5
        if rsi_h1_now is not None and rsi_h1_now > 70.0:
            score_short += 0.5

        PS = float(ctx["PS"])
        loss_streak = float(ctx["loss_streak"])
        min_score = 5.0
        if PS < 0.4 or loss_streak >= 2.0:
            min_score = 5.5

        # Final gates
        if over_long and reclaim_long and vol_ok and long_pat and long_ok_bias and score_long >= min_score:
            dist = px * tp_pct
            return Signal(type="BUY", reason="m1 reclaim long", stop_dist=dist, take_dist=dist, score=score_long, tf="m1")
        if over_short and reclaim_short and vol_ok and short_pat and short_ok_bias and score_short >= min_score:
            dist = px * tp_pct
            return Signal(type="SELL", reason="m1 reclaim short", stop_dist=dist, take_dist=dist, score=score_short, tf="m1")

        return Signal(type="WAIT", reason="Inside bands")
        

# ---------------- h1 mean reversion ----------------

class H1MeanReversion(Strategy):
    name = "h1 Mean‑Reversion"

    def evaluate(self, ctx: dict) -> Signal:
        h1 = ctx["h1"]; i = ctx["iC_h1"]
        if i is None or i < max(220, ctx.get("min_h1_bars", 220)):
            return Signal(type="WAIT", reason="Warmup")
        px = h1[i]["close"]
        a14 = atr(h1, 14); ax = adx(h1, 14)
        dc = donchian(h1, 20)
        VS = float(ctx["VS"])
        adx_cap = 17.0 * VS
        if (ax[i] or 0.0) > adx_cap:
            return Signal(type="WAIT", reason="Trend regime")
        hi = dc["hi"][i]; lo = dc["lo"][i]
        if hi is None or lo is None:
            return Signal(type="WAIT", reason="DC warmup")
        mid = 0.5 * (hi + lo); atr_abs = a14[i] or 0.0
        if atr_abs <= 0:
            return Signal(type="WAIT", reason="ATR warmup")

        # Base distances (ATR units)
        k_entry = 0.75; k_stop = 0.85; k_take = 0.95
        if (ax[i] or 0.0) < 14.0:
            k_entry = 0.85  # deeper entry in dead-range
        # Capitulation extension (bigger take)
        rsi_h1 = rsi([c["close"] for c in h1], 14); rsi_now = rsi_h1[i] or 50.0
        vol_win = [c.get("volume", 0.0) for c in h1[max(0, i - 20):i]]
        vmed = median(vol_win) if vol_win else 0.0
        capit = (
            (ax[i] or 0.0) < 14.0
            and (rsi_now < 30.0 or rsi_now > 70.0)
            and (h1[i].get("volume", 0.0) >= 2.0 * vmed if vmed > 0 else True)
        )
        if capit:
            k_take = 1.2 if VS <= 1.2 else 1.1

        dist = px - mid
        if dist <= -(k_entry * atr_abs):
            return Signal(type="BUY", reason="H1 mean‑revert up", stop_dist=k_stop * atr_abs, take_dist=k_take * atr_abs, score=3.5, tf="h1")
        if dist >= +(k_entry * atr_abs):
            return Signal(type="SELL", reason="H1 mean‑revert down", stop_dist=k_stop * atr_abs, take_dist=k_take * atr_abs, score=3.5, tf="h1")

        return Signal(type="WAIT", reason="Near mean")


# ---------------- h1 breakout ----------------

class H1Breakout(Strategy):
    name = "h1 Breakout"

    def evaluate(self, ctx: dict) -> Signal:
        h1 = ctx["h1"]; i = ctx["iC_h1"]
        if i is None or i < max(220, ctx.get("min_h1_bars", 220)):
            return Signal(type="WAIT", reason="Warmup")
        a14 = atr(h1, 14)
        dc = donchian(h1, 20)
        px = h1[i]["close"]
        # squeeze→expansion similar to V1; volume threshold scales with VS
        wnd = a14[max(0, i - 30):i]
        if len([x for x in wnd if x is not None]) < 10:
            return Signal(type="WAIT", reason="ATR warmup")
        med = median([x for x in wnd if x is not None])
        squeeze = (a14[i - 1] or 0.0) <= 0.6 * med
        tr_today = max(
            h1[i]["high"] - h1[i]["low"],
            abs(h1[i]["high"] - h1[i - 1]["close"]),
            abs(h1[i]["low"] - h1[i - 1]["close"]),
        )
        expand = tr_today >= 1.4 * med
        VS = float(ctx["VS"])
        v_win = [c.get("volume", 0.0) for c in h1[max(0, i - 20):i]]
        v_med = median(v_win) if v_win else 0.0
        mult = min(2.0, max(1.1, 1.3 * VS))
        vol_ok = (h1[i].get("volume", 0.0) >= mult * v_med) if v_med > 0 else True

        hi_prev = dc["hi"][i - 1]; lo_prev = dc["lo"][i - 1]
        up = (hi_prev is not None) and (px > hi_prev)
        dn = (lo_prev is not None) and (px < lo_prev)

        if not (squeeze and expand and vol_ok):
            return Signal(type="WAIT", reason="No breakout")

        # MACD cross in direction on the breakout bar (spec requirement)
        macd_l, macd_s = macd_line_signal([c["close"] for c in h1], 12, 26, 9)
        prev = (macd_l[i - 1] or 0.0) - (macd_s[i - 1] or 0.0)
        cur = (macd_l[i] or 0.0) - (macd_s[i] or 0.0)
        cross_up = prev <= 0 < cur
        cross_dn = prev >= 0 > cur
        if up and not cross_up:
            return Signal(type="WAIT", reason="No MACD confirm")
        if dn and not cross_dn:
            return Signal(type="WAIT", reason="No MACD confirm")

        if up:
            return Signal(type="BUY", reason="H1 breakout up", stop_dist=1.2 * (a14[i] or 0.0), take_dist=1.1 * (a14[i] or 0.0), score=5.0, tf="h1")
        if dn:
            return Signal(type="SELL", reason="H1 breakout down", stop_dist=1.2 * (a14[i] or 0.0), take_dist=1.1 * (a14[i] or 0.0), score=5.0, tf="h1")

        return Signal(type="WAIT", reason="Waiting break")


# ---------------- h1 trend ----------------

class H1Trend(Strategy):
    name = "h1 Trend‑Following"

    def evaluate(self, ctx: dict) -> Signal:
        h1 = ctx["h1"]; i = ctx["iC_h1"]
        if i is None or i < max(220, ctx.get("min_h1_bars", 220)):
            return Signal(type="WAIT", reason="Warmup")

        closes = [c["close"] for c in h1]
        e200 = ema(closes, 200)
        a14 = atr(h1, 14)
        ax = adx(h1, 14)
        dc = donchian(h1, 20)

        PS = float(ctx["PS"])
        thr = 25.0 * (1.0 - 0.20 * (1.0 - PS))   # higher PS → easier engage
        if (ax[i] or 0.0) < thr:
            return Signal(type="WAIT", reason="Trend weak")

        px = h1[i]["close"]
        ema_up = bool(e200[i] and e200[i] > e200[max(0, i - 5)])
        ema_dn = bool(e200[i] and e200[i] < e200[max(0, i - 5)])
        hi_prev = dc["hi"][i - 1]; lo_prev = dc["lo"][i - 1]
        bk_up = (px > hi_prev) if hi_prev is not None else False
        bk_dn = (px < lo_prev) if lo_prev is not None else False

        if ema_up and bk_up:
            return Signal(type="BUY", reason="Trend up + break", stop_dist=1.8 * (a14[i] or 0.0), take_dist=1.4 * (a14[i] or 0.0), score=5.0, tf="h1")
        if ema_dn and bk_dn:
            return Signal(type="SELL", reason="Trend down + break", stop_dist=1.8 * (a14[i] or 0.0), take_dist=1.4 * (a14[i] or 0.0), score=5.0, tf="h1")

        return Signal(type="WAIT", reason="Need Donchian break")


# ---------------- Router ----------------

class RouterV3(Strategy):
    """Implements regime & priority and picks a candidate signal."""
    name = "Router V3"

    def __init__(self):
        self.m1 = M1Scalp()
        self.h1_mr = H1MeanReversion()
        self.h1_bo = H1Breakout()
        self.h1_tr = H1Trend()

        # telemetry
        self.last_regime: Optional[str] = None
        self.last_bias: Optional[str] = None
        self.last_adx: Optional[float] = None
        self.last_atr_pct: Optional[float] = None
        self.last_strategy: Optional[str] = None

    def evaluate(self, ctx: dict) -> Signal:
        m1 = ctx["m1"]; h1 = ctx["h1"]
        iC_m1 = ctx.get("iC_m1"); iC_h1 = ctx.get("iC_h1")

        # regime by ADX (hysteresis: Trend ≥25, exit ≤21; Breakout ≤23 with DC break; else Range)
        ax_h1 = adx(h1, 14)
        adx_last = (ax_h1[iC_h1] or 0.0) if iC_h1 is not None else 0.0
        self.last_adx = adx_last

        A = atr(h1, 14)
        atr_pct = ((A[iC_h1] or 0.0) / max(1.0, h1[iC_h1]["close"])) if iC_h1 is not None else None
        self.last_atr_pct = atr_pct

        # bias via EMA200(h1)
        closes_h1 = [c["close"] for c in h1]
        e200 = ema(closes_h1, 200)
        if iC_h1 is not None and e200[iC_h1] is not None:
            self.last_bias = "Bullish" if h1[iC_h1]["close"] >= (e200[iC_h1] or 0.0) else "Bearish"
        else:
            self.last_bias = None

        # breakout presence
        dc = donchian(h1, 20)
        bk_up = bk_dn = False
        if iC_h1 is not None and iC_h1 > 0:
            px = h1[iC_h1]["close"]
            hi_prev = dc["hi"][iC_h1 - 1]
            lo_prev = dc["lo"][iC_h1 - 1]
            bk_up = (hi_prev is not None) and (px > hi_prev)
            bk_dn = (lo_prev is not None) and (px < lo_prev)

        if adx_last >= 25.0:
            regime = "Trend"
        elif (adx_last <= 23.0) and (bk_up or bk_dn) and (atr_pct is None or (0.0005 <= atr_pct <= 0.0175)):
            regime = "Breakout"
        else:
            regime = "Range"
        self.last_regime = regime

        # Priority: Trend → Breakout → Range
        sig: Signal = Signal(type="WAIT", reason="—")
        if regime == "Trend":
            sig = self.h1_tr.evaluate(ctx)
            self.last_strategy = self.h1_tr.name if sig.type != "WAIT" else None
            if sig.type != "WAIT":
                return sig

        if regime == "Breakout":
            sig = self.h1_bo.evaluate(ctx)
            self.last_strategy = self.h1_bo.name if sig.type != "WAIT" else None
            if sig.type != "WAIT":
                return sig

        # Range: try m1 scalp; if not, h1 mean‑reversion when ADX <= 17×VS
        sig = self.m1.evaluate(ctx)
        self.last_strategy = self.m1.name if sig.type != "WAIT" else None
        if sig.type != "WAIT":
            return sig

        # fall back to H1 MR if quiet enough
        VS = float(ctx["VS"])
        if adx_last <= 17.0 * VS:
            sig2 = self.h1_mr.evaluate(ctx)
            self.last_strategy = self.h1_mr.name if sig2.type != "WAIT" else None
            return sig2

        return Signal(type="WAIT", reason="No setup")
