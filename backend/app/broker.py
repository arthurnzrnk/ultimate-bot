# backend/app/strategies/breakout.py
from statistics import median
from .base import Strategy, Signal
from ..ta import atr, donchian, adx

class Breakout(Strategy):
    """Volatility squeeze → expansion breakout with tight initial risk."""
    name = "Breakout"

    def evaluate(self, ohlc: list[dict], ctx: dict) -> Signal:
        iC = ctx.get("iC")
        min_bars = ctx.get("min_bars", 120)  # need history for med ATR etc.
        if iC is None or iC < min_bars:
            return Signal(type="WAIT", reason="Need history")

        a14 = atr(ohlc, 14)
        px  = ohlc[iC]["close"]
        dc  = donchian(ohlc, ctx.get("donchian_len", 20))
        ax  = adx(ohlc, 14)

        # Squeeze: ATR below a fraction of its rolling median
        wnd = a14[max(0, iC - 30): iC] or []
        vals = [x for x in wnd if x is not None]
        if len(vals) < 10:
            return Signal(type="WAIT", reason="ATR warmup")
        med = median(vals)
        squeeze = (a14[iC - 1] or 0.0) <= ctx.get("squeeze_frac", 0.6) * med

        # Expansion: current true range jumps above a multiple of that median
        tr_today = max(
            ohlc[iC]["high"] - ohlc[iC]["low"],
            abs(ohlc[iC]["high"] - ohlc[iC - 1]["close"]),
            abs(ohlc[iC]["low"]  - ohlc[iC - 1]["close"])
        )
        expand = tr_today >= ctx.get("expand_k", 1.4) * med

        hi_prev = dc["hi"][iC - 1] if iC > 0 else None
        lo_prev = dc["lo"][iC - 1] if iC > 0 else None
        bk_up = bool(hi_prev is not None and px > hi_prev)
        bk_dn = bool(lo_prev is not None and px < lo_prev)

        if not (squeeze and expand):
            return Signal(type="WAIT", reason="No squeeze→expand")

        # Stops just inside the prior range (tight initial risk)
        stop_pad = ctx.get("bo_stop_pad_frac", 0.30)
        if bk_up:
            stop = max((px - (tr_today * (1.0 + stop_pad))), 1e-8)
            return Signal(type="BUY", reason="Volatility breakout up",
                          stop_dist=px - stop,
                          take_dist=(a14[iC] or 0.0) * 1.2,
                          score=6)
        if bk_dn:
            stop = max((px + (tr_today * (1.0 + stop_pad))), 1e-8)
            return Signal(type="SELL", reason="Volatility breakout down",
                          stop_dist=stop - px,
                          take_dist=(a14[iC] or 0.0) * 1.2,
                          score=6)

        return Signal(type="WAIT", reason="Waiting breakout close")
