# backend/app/strategies/trend_follow.py
from .base import Strategy, Signal
from ..ta import ema, atr, adx, donchian

class TrendFollow(Strategy):
    """Donchian breakout in direction of EMA200 bias; ATR stop/take."""
    name = "Trend‑Following"

    def evaluate(self, ohlc: list[dict], ctx: dict) -> Signal:
        iC = ctx.get("iC")
        # Need >=200 bars for EMA200 + a buffer
        if iC is None or iC < ctx.get("min_bars", 220):
            return Signal(type="WAIT", reason="Need history")

        closes = [c["close"] for c in ohlc]
        ema200 = ema(closes, 200)
        a14    = atr(ohlc, 14)
        ax     = adx(ohlc, 14)
        dc     = donchian(ohlc, ctx.get("donchian_len", 20))

        px     = ohlc[iC]["close"]
        emaUp  = bool(ema200[iC] and ema200[iC] > ema200[max(0, iC - 5)])
        emaDn  = bool(ema200[iC] and ema200[iC] < ema200[max(0, iC - 5)])
        adxOK  = (ax[iC] or 0.0) >= ctx.get("adx_trend_min", 25)
        stop   = (a14[iC] or 0.0) * ctx.get("trend_stop_atr", 2.0)
        take   = (a14[iC] or 0.0) * ctx.get("trend_take_atr", 1.5)

        hi_prev = dc["hi"][iC - 1] if iC > 0 else None
        lo_prev = dc["lo"][iC - 1] if iC > 0 else None
        bkUp = bool(hi_prev is not None and px > hi_prev)
        bkDn = bool(lo_prev is not None and px < lo_prev)

        if not adxOK:
            return Signal(type="WAIT", reason="Trend weak (ADX)")

        if emaUp and bkUp:
            return Signal(type="BUY", reason="Donchian break + EMA200 up",
                          stop_dist=stop, take_dist=take, score=5)
        if emaDn and bkDn:
            return Signal(type="SELL", reason="Donchian break + EMA200 down",
                          stop_dist=stop, take_dist=take, score=5)

        return Signal(type="WAIT", reason="Waiting Donchian break")
