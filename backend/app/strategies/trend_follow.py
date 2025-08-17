from .base import Strategy, Signal
from ..ta import ema, atr, adx, donchian

class TrendFollow(Strategy):
    """Donchian breakout in direction of EMA200 bias; ATR stop/trail."""
    name = "Trend‑Following"

    def evaluate(self, h1: list[dict], ctx: dict) -> Signal:
        iC = ctx.get("iC_h1")
        if iC is None or iC < ctx.get("min_h1_bars", 220):
            return Signal(type="WAIT", reason="Need H1 history")

        closes = [c["close"] for c in h1]
        ema200 = ema(closes, 200)
        a14    = atr(h1, 14)
        ax     = adx(h1, 14)
        dc     = donchian(h1, ctx.get("donchian_len", 20))

        px     = h1[iC]["close"]
        emaUp  = ema200[iC] and ema200[iC] > ema200[max(0, iC-5)]
        emaDn  = ema200[iC] and ema200[iC] < ema200[max(0, iC-5)]
        adxOK  = (ax[iC] or 0.0) >= ctx.get("adx_trend_min", 25)
        stop   = (a14[iC] or 0.0) * ctx.get("trend_stop_atr", 2.0)
        take   = (a14[iC] or 0.0) * ctx.get("trend_take_atr", 1.5)

        hi_prev = dc["hi"][max(0, iC-1)]
        lo_prev = dc["lo"][max(0, iC-1)]
        bkUp = (px > hi_prev) if hi_prev is not None else False
        bkDn = (px < lo_prev) if lo_prev is not None else False

        if not adxOK:
            return Signal(type="WAIT", reason="Trend weak (ADX)")

        if emaUp and bkUp:
            return Signal(type="BUY", reason="Donchian break + EMA200 up",
                          stop_dist=stop, take_dist=take, score=5)
        if emaDn and bkDn:
            return Signal(type="SELL", reason="Donchian break + EMA200 down",
                          stop_dist=stop, take_dist=take, score=5)

        return Signal(type="WAIT", reason="Waiting Donchian break")
