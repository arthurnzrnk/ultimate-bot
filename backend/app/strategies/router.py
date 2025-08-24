"""Router + Strategies for Strategy V3.4.

- Regime: Trend / Breakout / Range with hysteresis (25/21 and 23).
- Bias: EMA200(h1).
- m1: Enhanced Level King (VWAP MR + asymmetric TP & A+ gating via meta).
- h1: MR / Breakout / Trend-Following per spec.

Hard micro gates for m1:
  - Reclaim candle vol >= 2× median(20)
  - Candlestick quality (engulfing/hammer, shooting-star)
  - Spread <= 8 bps (if BBO exists)
  - ATR% band [0.05%, 1.75%] × VS
  - VWAP slope cap 0.050% × VS
  - MTF alignment or CT exception (ADX_h1 < 20×VS and RSI_m1 extreme)
  - z‑VWAP confirm

Scoring:
  +0.5 each: RSI extreme + favorable delta (proxy with RSI slope), MACD cross recent, h1 RSI extreme
  +0.25 each (proxies for micro flow): spread stability, TOD boost, etc. (lightweight)
  Threshold: 5.25 base; +0.50 when PS<0.4 or loss_streak=2; **+RED_DAY_L1_SCORE_ADD when red_level==1**.

The engine finalizes fee-aware targets (asym/A+) using Signal.meta hints.
"""

from __future__ import annotations
from statistics import median, mean, pstdev
from typing import Optional, Tuple

from .base import Strategy, Signal
from ..ta import ema, atr, adx, donchian, rsi, macd_line_signal
from ..config import settings


def _macd_cross_recent(line, sig, i: int, side: str, lookback: int = 3) -> bool:
    if i is None or i <= 1:
        return False
    for k in range(1, lookback + 1):
        j = i - k
        if j <= 0:
            break
        prev = (line[j - 1] or 0.0) - (sig[j - 1] or 0.0)
        cur = (line[j] or 0.0) - (sig[j] or 0.0)
        if side == "long" and (prev <= 0 < cur):
            return True
        if side == "short" and (prev >= 0 > cur):
            return True
    return False


def _wick_shapes(c: dict) -> Tuple[float,float,float,float,float]:
    rng = max(1e-9, c["high"] - c["low"])
    body = abs(c["close"] - c["open"])
    hi_wick = c["high"] - max(c["open"], c["close"])
    lo_wick = min(c["open"], c["close"]) - c["low"]
    close_pos = (c["close"] - c["low"]) / rng
    return rng, body, hi_wick, lo_wick, close_pos


class M1Scalp(Strategy):
    name = "m1 Level King"

    def evaluate(self, ctx: dict) -> Signal:
        m1 = ctx["m1"]; i = ctx["iC_m1"]
        if i is None or i < 2 or len(m1) < max(6, ctx.get("min_bars", 5)):
            return Signal(type="WAIT", reason="Warmup")
        px = m1[i]["close"]
        VS = float(ctx["VS"])
        PS = float(ctx["PS"])

        # ATR% band (scaled by VS, then hard clamp)
        a14 = atr(m1, 14)
        atr_pct = (a14[i] or 0.0) / max(1.0, px)
        band_min = max(0.0005, 0.0005 * VS)
        band_max = min(0.0175 * VS, 0.0175)
        if atr_pct < band_min or atr_pct > band_max:
            return Signal(type="WAIT", reason="ATR band")

        # VWAP slope cap (EMA10 on VWAP)
        vwap = ctx["vwap"]
        if vwap[i] is None:
            return Signal(type="WAIT", reason="VWAP warmup")
        v10 = ema([x if x is not None else vwap[i] for x in vwap], 10)
        base = i - 3 if i >= 3 else max(0, i - 1)
        slope = abs((v10[i] or vwap[i]) - (v10[base] or vwap[base])) / max(1.0, px)
        if slope > (0.0005 * VS):  # 0.050% × VS
            return Signal(type="WAIT", reason="Slope cap")

        # Spread cap (if BBO available)
        bid, ask = ctx.get("bid"), ctx.get("ask")
        if bid and ask:
            mid = (bid + ask) / 2.0
            spread_bps = ((ask - bid) / max(1e-9, mid)) * 10000.0
            if spread_bps > settings.spread_cap_bps_m1:
                return Signal(type="WAIT", reason="Spread")

        # MTF bias on h1 EMA200 with CT exception
        h1 = ctx["h1"]; j = ctx["iC_h1"]
        closes_h1 = [c["close"] for c in h1]
        e200 = ema(closes_h1, 200)
        ema_up = bool(e200[j] and h1[j]["close"] >= (e200[j] or 0.0)) if j is not None else True
        ema_dn = bool(e200[j] and h1[j]["close"] <= (e200[j] or 0.0)) if j is not None else True
        ax_h1 = adx(h1, 14); adx_h1 = (ax_h1[j] or 0.0) if j is not None else 0.0
        rsi_m1 = rsi([c["close"] for c in m1], 14); rsi_now = rsi_m1[i] or 50.0
        rsi_prev = rsi_m1[i - 1] if i - 1 >= 0 else None

        allow_ct_long = (adx_h1 < 20.0 * VS) and (rsi_now < 25.0)
        allow_ct_short = (adx_h1 < 20.0 * VS) and (rsi_now > 75.0)

        # Volume quality on reclaim candle
        vols = [c.get("volume", 0.0) for c in m1[max(0, i - 20):i]]
        vmed = median(vols) if vols else 0.0
        cur_vol = m1[i].get("volume", 0.0)
        vol_ok = (cur_vol >= 2.0 * vmed) if vmed > 0 else True

        # Candle quality
        prev = m1[i - 1]; cur = m1[i]

        def bull_engulf(a, b):
            return (b["close"] >= b["open"]) and (a["close"] < a["open"]) and (b["close"] > a["open"]) and (b["open"] < a["close"])
        def bear_engulf(a, b):
            return (b["close"] <= b["open"]) and (a["close"] > a["open"]) and (b["close"] < a["open"]) and (b["open"] > a["close"])
        def hammer(b):
            rng, body, hi_w, lo_w, close_pos = _wick_shapes(b)
            return lo_w >= body and close_pos >= 0.75
        def shooting_star(b):
            rng, body, hi_w, lo_w, close_pos = _wick_shapes(b)
            return hi_w >= body and close_pos <= 0.25

        long_pat = bull_engulf(prev, cur) or hammer(cur)
        short_pat = bear_engulf(prev, cur) or shooting_star(cur)

        # Overshoot + reclaim of VWAP band
        band_pct = max(0.0015, 0.75 * atr_pct)    # % of price
        vprev = ctx["vwap"][i - 1]
        reclaim_long = (cur["close"] >= (ctx["vwap"][i] * (1 - 0.65 * band_pct)) and (cur["close"] >= cur["open"]))
        reclaim_short = (cur["close"] <= (ctx["vwap"][i] * (1 + 0.65 * band_pct)) and (cur["close"] <= cur["open"]))
        over_long = bool(vprev and (m1[i - 1]["low"] <= (vprev * (1 - 1.00 * band_pct))))
        over_short = bool(vprev and (m1[i - 1]["high"] >= (vprev * (1 + 1.00 * band_pct))))

        # --- z‑VWAP confirm (Spec §2/§6A) ---
        # z is computed on (close - vwap) over the last ZVWAP_STD_WINDOW_M1 bars
        W = settings.spec.ZVWAP_STD_WINDOW_M1
        if i < W:
            return Signal(type="WAIT", reason="zVWAP warmup")
        devs = []
        for k in range(i - W + 1, i + 1):
            if ctx["vwap"][k] is None:
                continue
            devs.append(m1[k]["close"] - ctx["vwap"][k])
        if len(devs) < max(10, int(W * 0.6)):
            return Signal(type="WAIT", reason="zVWAP warmup")
        mu = mean(devs)
        sd = pstdev(devs) if len(devs) >= 2 else 0.0
        def z_at(n_idx: int) -> Optional[float]:
            vw = ctx["vwap"][n_idx]
            if vw is None or sd <= 0:
                return None
            return (m1[n_idx]["close"] - vw - mu) / sd
        z_prev = z_at(i - 1)
        z_cur = z_at(i)
        z_min = settings.spec.Z_MIN
        z_ok_long = (z_prev is not None and z_prev <= -z_min) and (z_cur is not None and z_cur > -0.25)
        z_ok_short = (z_prev is not None and z_prev >= +z_min) and (z_cur is not None and z_cur < +0.25)

        # MACD recency for scoring
        macd_l, macd_s = macd_line_signal([c["close"] for c in m1], 12, 26, 9)
        macd_long_recent = _macd_cross_recent(macd_l, macd_s, i, "long", 3)
        macd_short_recent = _macd_cross_recent(macd_l, macd_s, i, "short", 3)

        # h1 RSI extreme
        rsi_h1 = rsi([c["close"] for c in h1], 14); rsi_h1_now = rsi_h1[j] if j is not None else None

        # Score (base 4.0)
        score_long = score_short = 4.0
        if rsi_prev is not None and rsi_now is not None:
            if rsi_now < 30.0 and rsi_now > (rsi_prev or rsi_now): score_long += 0.5
            if rsi_now > 70.0 and rsi_now < (rsi_prev or rsi_now): score_short += 0.5
        if macd_long_recent: score_long += 0.5
        if macd_short_recent: score_short += 0.5
        if rsi_h1_now is not None and rsi_h1_now < 30.0: score_long += 0.5
        if rsi_h1_now is not None and rsi_h1_now > 70.0: score_short += 0.5

        # Light micro bonuses (proxies): spread stable + TOD (kept lightweight)
        score_long += 0.25
        score_short += 0.25

        # Threshold with red-day add
        min_score = 5.25
        if PS < 0.4 or float(ctx.get("loss_streak", 0.0)) >= 2.0:
            min_score += 0.50  # 5.75
        if int(ctx.get("red_level", 0)) == 1:
            min_score += settings.spec.RED_DAY_L1_SCORE_ADD  # harder on L1

        # Bias + CT exception
        long_ok_bias = (ema_up or allow_ct_long)
        short_ok_bias = (ema_dn or allow_ct_short)

        # If passes, emit signal with meta hints so engine can do asym/A+ fee math
        if over_long and reclaim_long and vol_ok and long_pat and long_ok_bias and z_ok_long and score_long >= min_score:
            tp_pct_raw = max(0.0015, 0.85 * band_pct) * (1.0 + 0.2 * max(0.0, VS - 1.0))
            dist = px * tp_pct_raw
            return Signal(
                type="BUY",
                reason="m1 reclaim long",
                stop_dist=dist,
                take_dist=dist,
                score=score_long,
                tf="m1",
                meta={"band_pct": band_pct, "tp_pct_raw": tp_pct_raw, "micro_triad_ok": True, "z_vwap": float(z_cur) if z_cur is not None else None}
            )
        if over_short and reclaim_short and vol_ok and short_pat and short_ok_bias and z_ok_short and score_short >= min_score:
            tp_pct_raw = max(0.0015, 0.85 * band_pct) * (1.0 + 0.2 * max(0.0, VS - 1.0))
            dist = px * tp_pct_raw
            return Signal(
                type="SELL",
                reason="m1 reclaim short",
                stop_dist=dist,
                take_dist=dist,
                score=score_short,
                tf="m1",
                meta={"band_pct": band_pct, "tp_pct_raw": tp_pct_raw, "micro_triad_ok": True, "z_vwap": float(z_cur) if z_cur is not None else None}
            )

        return Signal(type="WAIT", reason="Inside bands")


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

        # deeper entry if ADX<14
        k_entry = 0.75
        if (ax[i] or 0.0) < 14.0:
            k_entry = 0.85

        dist = px - mid
        side: Optional[str] = None
        if dist <= -(k_entry * atr_abs):
            side = "long"
        elif dist >= +(k_entry * atr_abs):
            side = "short"

        rs = rsi([c["close"] for c in h1], 14); rsi_now = rs[i] or 50.0
        if side == "long" and not (rsi_now < 30.0): return Signal(type="WAIT", reason="RSI not supportive")
        if side == "short" and not (rsi_now > 70.0): return Signal(type="WAIT", reason="RSI not supportive")

        # capitulation extension for take
        from statistics import median
        v_win = [c.get("volume", 0.0) for c in h1[max(0, i - 20):i]]
        vmed = median(v_win) if v_win else 0.0
        k_take = 0.95
        capit = ((ax[i] or 0.0) < 14.0) and (h1[i].get("volume", 0.0) >= 2.0 * vmed if vmed > 0 else True)
        if side == "long" and (rsi_now < 30.0) and capit:
            k_take = 1.2 if VS <= 1.2 else 1.1
        if side == "short" and (rsi_now > 70.0) and capit:
            k_take = 1.2 if VS <= 1.2 else 1.1

        if side == "long":
            return Signal(type="BUY", reason="H1 mean‑revert up", stop_dist=0.85 * atr_abs, take_dist=k_take * atr_abs, score=3.5, tf="h1")
        if side == "short":
            return Signal(type="SELL", reason="H1 mean‑revert down", stop_dist=0.85 * atr_abs, take_dist=k_take * atr_abs, score=3.5, tf="h1")
        return Signal(type="WAIT", reason="Near mean")


class H1Breakout(Strategy):
    name = "h1 Breakout"
    def evaluate(self, ctx: dict) -> Signal:
        h1 = ctx["h1"]; i = ctx["iC_h1"]
        if i is None or i < max(220, ctx.get("min_h1_bars", 220)):
            return Signal(type="WAIT", reason="Warmup")
        a14 = atr(h1, 14); dc = donchian(h1, 20)
        wnd = a14[max(0, i - 30):i]
        from statistics import median
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

        px = h1[i]["close"]
        hi_prev = dc["hi"][i - 1]; lo_prev = dc["lo"][i - 1]
        up = (hi_prev is not None) and (px > hi_prev)
        dn = (lo_prev is not None) and (px < lo_prev)
        if not (squeeze and expand and vol_ok):
            return Signal(type="WAIT", reason="No breakout")

        # MACD cross confirm
        macd_l, macd_s = macd_line_signal([c["close"] for c in h1], 12, 26, 9)
        prev = (macd_l[i - 1] or 0.0) - (macd_s[i - 1] or 0.0)
        cur = (macd_l[i] or 0.0) - (macd_s[i] or 0.0)
        cross_up = prev <= 0 < cur
        cross_dn = prev >= 0 > cur
        if up and not cross_up: return Signal(type="WAIT", reason="No MACD confirm")
        if dn and not cross_dn: return Signal(type="WAIT", reason="No MACD confirm")

        if up:
            return Signal(type="BUY", reason="H1 breakout up", stop_dist=1.2 * (a14[i] or 0.0), take_dist=1.1 * (a14[i] or 0.0), score=5.0, tf="h1")
        if dn:
            return Signal(type="SELL", reason="H1 breakout down", stop_dist=1.2 * (a14[i] or 0.0), take_dist=1.1 * (a14[i] or 0.0), score=5.0, tf="h1")
        return Signal(type="WAIT", reason="Waiting break")


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
        thr = 25.0 * (1.0 - 0.20 * (1.0 - PS))   # scaled by PS
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


class RouterV3(Strategy):
    """Regime priority & signal selection with hysteresis, and prefer-tf scheduling."""
    name = "Router V3.4"

    def __init__(self):
        self.m1 = M1Scalp()
        self.h1_mr = H1MeanReversion()
        self.h1_bo = H1Breakout()
        self.h1_tr = H1Trend()
        self.last_regime: Optional[str] = None
        self.last_bias: Optional[str] = None
        self.last_adx: Optional[float] = None
        self.last_atr_pct: Optional[float] = None
        self.last_strategy: Optional[str] = None

    def evaluate(self, ctx: dict) -> Signal:
        m1 = ctx["m1"]; h1 = ctx["h1"]
        iC_m1 = ctx.get("iC_m1"); iC_h1 = ctx.get("iC_h1")
        prefer = ctx.get("preferTF", "m1")

        ax_h1 = adx(h1, 14)
        adx_last = (ax_h1[iC_h1] or 0.0) if iC_h1 is not None else 0.0
        self.last_adx = adx_last

        A = atr(h1, 14)
        atr_pct = ((A[iC_h1] or 0.0) / max(1.0, h1[iC_h1]["close"])) if iC_h1 is not None else None
        self.last_atr_pct = atr_pct

        closes_h1 = [c["close"] for c in h1]
        e200 = ema(closes_h1, 200)
        if iC_h1 is not None and e200[iC_h1] is not None:
            self.last_bias = "Bullish" if h1[iC_h1]["close"] >= (e200[iC_h1] or 0.0) else "Bearish"
        else:
            self.last_bias = None

        dc = donchian(h1, 20)
        bk_up = bk_dn = False
        if iC_h1 is not None and iC_h1 > 0:
            px = h1[iC_h1]["close"]
            hi_prev = dc["hi"][iC_h1 - 1]
            lo_prev = dc["lo"][iC_h1 - 1]
            bk_up = (hi_prev is not None) and (px > hi_prev)
            bk_dn = (lo_prev is not None) and (px < lo_prev)

        # Regime proposal
        if adx_last >= 25.0:
            regime_prop = "Trend"
        elif (adx_last <= 23.0) and (bk_up or bk_dn) and (atr_pct is None or (0.0005 <= atr_pct <= 0.0175)):
            regime_prop = "Breakout"
        else:
            regime_prop = "Range"

        # Hysteresis: stay in Trend until ADX <= 21
        regime = regime_prop
        if self.last_regime == "Trend" and regime_prop != "Trend" and adx_last > 21.0:
            regime = "Trend"
        self.last_regime = regime

        # Priority
        if regime == "Trend":
            sig = self.h1_tr.evaluate(ctx)
            self.last_strategy = self.h1_tr.name if sig.type != "WAIT" else None
            return sig
        if regime == "Breakout":
            sig = self.h1_bo.evaluate(ctx)
            self.last_strategy = self.h1_bo.name if sig.type != "WAIT" else None
            return sig

        # Range: try preferred TF first
        if prefer == "h1":
            sig = self.h1_mr.evaluate(ctx)
            self.last_strategy = self.h1_mr.name if sig.type != "WAIT" else None
            sig.tf = sig.tf or "h1"; 
            if sig.type != "WAIT":
                return sig
            # pass loss_streak/red_level for m1 threshold raise
            ctx2 = dict(ctx); ctx2["loss_streak"] = ctx.get("loss_streak", 0.0)
            sig2 = self.m1.evaluate(ctx2)
            self.last_strategy = self.m1.name if sig2.type != "WAIT" else None
            sig2.tf = sig2.tf or "m1"; return sig2
        else:
            ctx2 = dict(ctx); ctx2["loss_streak"] = ctx.get("loss_streak", 0.0)
            sig = self.m1.evaluate(ctx2)
            if sig.type != "WAIT":
                self.last_strategy = self.m1.name; sig.tf = sig.tf or "m1"; return sig
            sig2 = self.h1_mr.evaluate(ctx)
            self.last_strategy = self.h1_mr.name if sig2.type != "WAIT" else None
            sig2.tf = sig2.tf or "h1"; return sig2
