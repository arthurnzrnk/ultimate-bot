"""Core trading engine for the Ultimate Bot.

The engine runs continuously, polling market data, updating candles,
evaluating the currently selected strategy and executing trades via the
``PaperBroker``. It exposes methods to start the loop and to handle
bar‑close logic for both scalping and trend modes. The engine relies on
``StrategyRouter`` to select the active strategy based on user settings.
"""

import asyncio
import time
from datetime import datetime
from .config import settings
from .datafeed import seed_klines, poll_coinbase_tick
from .broker import PaperBroker, FEE_MAKER, FEE_TAKER
from .ta import ema
from .strategies.router import StrategyRouter


def sod_sec() -> int:
    """Return the current day's start timestamp in seconds."""
    return int((int(time.time()) // 86400) * 86400)


class BotEngine:
    """Main engine orchestrating data feed, strategy evaluation and trading."""

    def __init__(self) -> None:
        self.client = None
        self.m1: list[dict] = []
        self.h1: list[dict] = []
        self.vwap: list[float | None] = []
        self.bid: float | None = None
        self.ask: float | None = None
        self.price: float | None = None
        self.status_text: str = "Loading..."
        self.profile: dict = {
            "ATR_PCT_MIN": 0.0004,
            "ATR_PCT_MAX": 0.0200,
            "VWAP_SLOPE_MAX": 0.00060,
            "SPREAD_BPS_MAX": 10,
            "TP_FLOOR": 0.0020,
            "FEE_R_MAX": 0.25,
            "RISK_PCT_SCALP": 0.010,
            "LEV_CAP": 8,
        }
        self.settings: dict = {
            "scalp_mode": True,
            "auto_trade": True,
            "strategy": "Level King — Regime",
            "macro_pause": False,
        }
        self.router = StrategyRouter()
        self.broker = PaperBroker(start_equity=settings.start_equity)
        self._cool_until: int = 0
        self._last_bar_time_m1: int = 0
        self._last_bar_time_h1: int = 0
        self._loss_streak: int = 0

    async def start(self, client) -> None:
        """Begin polling and trading loop using the provided HTTP client."""
        self.client = client
        self.m1, self.h1 = await seed_klines(client)
        self._rebuild_vwap()
        asyncio.create_task(self._run())

    def _push_trade_to_m1(self, price: float, iso: str) -> None:
        """Push a new trade tick into the m1 candle series."""
        if "T" in iso:
            ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        else:
            ts = float(iso)
        t_ms = int(ts * 1000)
        bucket = (t_ms // 60000) * 60000
        t = bucket // 1000
        if not self.m1 or self.m1[-1]["time"] != t:
            self.m1.append({
                "time": t,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
            })
            self.m1 = self.m1[-3000:]
        else:
            c = self.m1[-1]
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price

    def _aggregate_h1(self) -> None:
        """Aggregate the m1 candles into h1 candles."""
        bars = {}
        for c in self.m1:
            bucket = (c["time"] // 3600) * 3600
            b = bars.get(bucket)
            if not b:
                bars[bucket] = {
                    "time": bucket,
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"],
                }
            else:
                b["high"] = max(b["high"], c["high"])
                b["low"] = min(b["low"], c["low"])
                b["close"] = c["close"]
                b["volume"] += c["volume"]
        self.h1 = sorted(bars.values(), key=lambda x: x["time"])

    def _rebuild_vwap(self) -> None:
        """Recompute VWAP for the current m1 series."""
        out = []
        day = None
        pv = 0.0
        vv = 0.0
        for c in self.m1:
            d = datetime.utcfromtimestamp(c["time"]).strftime("%Y-%m-%d")
            if day != d:
                day = d
                pv = 0.0
                vv = 0.0
            tp = (c["high"] + c["low"] + c["close"]) / 3.0
            v = max(1e-8, c.get("volume", 0.0))
            pv += tp * v
            vv += v
            out.append(pv / max(1e-8, vv))
        self.vwap = out

    def _day_pnl(self) -> float:
        sod = sod_sec()
        return sum([t.pnl for t in self.broker.history if (t.close_time or t.open_time) >= sod])

    def _fills_today(self) -> int:
        sod = sod_sec()
        return sum(1 for t in self.broker.history if (t.close_time or t.open_time) >= sod) + (1 if self.broker.pos else 0)

    async def _run(self) -> None:
        """Main loop: poll data, update series, evaluate strategies and execute."""
        last_m1_closed = 0
        last_h1_closed = 0
        while True:
            try:
                px, bid, ask = await poll_coinbase_tick(self.client)
                if bid and ask:
                    self.bid, self.ask = bid, ask
                shown = ((bid + ask) / 2.0) if (bid and ask) else (px or None)
                if shown is not None:
                    self.price = shown
                    iso = datetime.utcnow().isoformat() + "Z"
                    self._push_trade_to_m1(shown, iso)
                    self._rebuild_vwap()
                    self._aggregate_h1()

                # mark to market every second
                if self.broker.pos and self.price is not None:
                    _ = self.broker.mark(self.price)
                    p = self.broker.pos
                    # check hard stops
                    hit_stop = (self.price <= p.stop) if p.side == "long" else (self.price >= p.stop)
                    hit_take = (self.price >= p.take) if p.side == "long" else (self.price <= p.take)
                    if hit_stop or hit_take:
                        net = self.broker.close(p.take if hit_take else self.price)
                        if net is not None and net < 0:
                            self._loss_streak += 1
                        if self._loss_streak >= 3:
                            self._cool_until = int(time.time()) + 1800
                            self.status_text = "Cooling off after losses"
                            self._loss_streak = 0

                now = int(time.time())
                # check closed bars
                if len(self.m1) >= 2 and self.m1[-2]["time"] != last_m1_closed:
                    last_m1_closed = self.m1[-2]["time"]
                    await self._maybe_signal(tf="m1")
                if len(self.h1) >= 2 and self.h1[-2]["time"] != last_h1_closed:
                    last_h1_closed = self.h1[-2]["time"]
                    await self._maybe_signal(tf="h1")
            except Exception as e:
                self.status_text = f"Error loop: {e}"
            await asyncio.sleep(1.0)

    async def _maybe_signal(self, tf: str) -> None:
        """Evaluate the selected strategy and open/close trades."""
        if self.settings.get("macro_pause"):
            self.status_text = "Macro pause"
            return
        if int(time.time()) < self._cool_until:
            return
        pnl = self._day_pnl()
        fills = self._fills_today()
        daily_ok = (pnl < 500) and (pnl > -500) and (fills < 60)
        last_open = self.broker.history[-1].open_time if self.broker.history else 0
        now = int(time.time())
        cooldown_ok = (now - last_open) > (60 if self.settings.get("scalp_mode") else 3600)
        context = {
            "daily_ok": daily_ok,
            "cooldown_ok": cooldown_ok,
            "fills": fills,
            "max_fills": 60,
            "fee_rate": FEE_MAKER,
            "fee_taker": FEE_TAKER,
            "profile": self.profile,
            "vwap": self.vwap,
            "bid": self.bid,
            "ask": self.ask,
            "min_bars": 5,
            "min_bars_h1": 60,
        }
        if self.settings.get("scalp_mode"):
            src = self.m1
            iC = len(self.m1) - 2
        else:
            src = self.h1
            iC = len(self.h1) - 2
        context["iC"] = iC
        strategy = self.router.pick(self.settings.get("scalp_mode", True))
        sig = strategy.evaluate(src, context)
        # update status text
        self.status_text = (
            "Considering entering now" if sig.type in ("BUY", "SELL") else sig.reason
        )
        # handle trade
        if sig.type in ("BUY", "SELL") and self.settings.get("auto_trade") and self.price is not None:
            entry = src[iC]["close"]
            stopd = sig.stop_dist or (entry * 0.005)
            taked = sig.take_dist or (entry * 0.005)
            risk_pct = (
                self.profile.get("RISK_PCT_SCALP", 0.01)
                if self.settings.get("scalp_mode")
                else 0.003
            )
            risk_usd = self.broker.equity * risk_pct
            qty = max(0.0001, risk_usd / max(1.0, stopd))
            notional_cap = self.broker.equity * (
                self.profile.get("LEV_CAP", 8)
                if self.settings.get("scalp_mode")
                else 1
            )
            qty = min(qty, max(0.0001, notional_cap / max(1.0, entry)))
            stop = (entry - stopd) if sig.type == "BUY" else (entry + stopd)
            take = (entry + taked) if sig.type == "BUY" else (entry - taked)
            # reverse if opposite side open
            if self.broker.pos:
                ps = self.broker.pos.side
                if (sig.type == "BUY" and ps == "short") or (sig.type == "SELL" and ps == "long"):
                    self.broker.close(entry)
            if not self.broker.pos:
                self.broker.open(sig.type, entry, qty, stop, take, stopd, maker=True)