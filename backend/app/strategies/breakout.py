from statistics import median
from .base import Strategy, Signal
from ..ta import atr, donchian, adx

class Breakout(Strategy):
    """Volatility squeeze → expansion breakout with profile volume gate."""
    name = "Breakout"

    def evaluate(self, h1: list[dict], ctx: dict) -> Signal:
        iC = ctx.get("iC_h1")
        if iC is None or iC < ctx.get("min_h1_bars", 120):
            return Signal(type="WAIT", reason="Need H1 history")

        a14 = atr(h1, 14)
        px  = h1[iC]["close"]
        dc  = donchian(h1, ctx.get("donchian_len", 20))
        ax  = adx(h1, 14)

        wnd  = a14[max(0, iC-30):iC] or []
        if len(wnd) < 10:
            return Signal(type="WAIT", reason="ATR warmup")
        med  = median([x for x in wnd if x is not None])
        squeeze = (a14[iC-1] or 0.0) <= ctx.get("squeeze_frac", 0.6) * med

        tr_today = max(
            h1[iC]["high"] - h1[iC]["low"],
            abs(h1[iC]["high"] - h1[iC-1]["close"]),
            abs(h1[iC]["low"]  - h1[iC-1]["close"])
        )
        expand = tr_today >= ctx.get("expand_k", 1.4) * med

        hi_prev = dc["hi"][max(0, iC-1)]
        lo_prev = dc["lo"][max(0, iC-1)]
        bkUp = (px > hi_prev) if hi_prev is not None else False
        bkDn = (px < lo_prev) if lo_prev is not None else False

        # Profile volume gate on the breakout candle
        v_win = [c.get("volume", 0.0) for c in h1[max(0, iC-20):iC]]
        v_med = median(v_win) if v_win else 0.0
        vol_mult = float(ctx.get("breakout_vol_mult", 1.2))
        vol_ok = (h1[iC].get("volume", 0.0) >= vol_mult * v_med) if v_med > 0 else True

        if not (squeeze and expand):
            return Signal(type="WAIT", reason="No squeeze→expand")
        if not vol_ok:
            return Signal(type="WAIT", reason="Breakout vol gate")

        stop_pad = ctx.get("bo_stop_pad_frac", 0.30)
        if bkUp:
            stop = max((px - (tr_today * (1.0 + stop_pad))), 1e-8)
            return Signal(type="BUY", reason="Volatility breakout up",
                          stop_dist=px - stop, take_dist=(a14[iC] or 0.0)*1.2, score=6)
        if bkDn:
            stop = max((px + (tr_today * (1.0 + stop_pad))), 1e-8)
            return Signal(type="SELL", reason="Volatility breakout down",
                          stop_dist=stop - px, take_dist=(a14[iC] or 0.0)*1.2, score=6)

        return Signal(type="WAIT", reason="Waiting breakout close")
