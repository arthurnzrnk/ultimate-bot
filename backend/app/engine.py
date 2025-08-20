"""Core trading engine for the Ultimate Bot — Strategy V2 (profile-aware)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from statistics import median

from .config import settings
from .datafeed import seed_klines, poll_tick
from .broker import PaperBroker, FEE_MAKER, FEE_TAKER
from .strategies.router import StrategyRouter
from .ta import atr, donchian  # (ema removed as unused)


def sod_sec() -> int:
    return int((int(time.time()) // 86400) * 86400)


def _normalize_hyphens(s: str) -> str:
    if not isinstance(s, str):
        return s
    return (
        s.replace("\u2011", "-")
         .replace("\u2013", "-")
         .replace("\u2014", "-")
         .replace("\u2010", "-")
         .replace("\u2212", "-")
    )


def _mmss(sec: int) -> str:
    sec = max(0, int(sec))
    m = sec // 60
    s = sec % 60
    return f"{m}:{s:02d}"


class BotEngine:
    """Main engine orchestrating data feed, strategy evaluation and trading."""

    def __init__(self) -> None:
        self.client = None
        self.m1: list[dict[str, Any]] = []
        self.h1: list[dict[str, Any]] = []
        self.vwap: list[float | None] = []

        self.bid: float | None = None
        self.ask: float | None = None
        self.price: float | None = None

        self.status_text: str = "Loading..."
        self.logs: list[dict[str, Any]] = []

        self._PROFILES = {
            "LIGHT": {
                "ATR_PCT_MIN": 0.0004,
                "ATR_PCT_MAX": 0.0200,
                "VWAP_SLOPE_MAX": 0.00060,
                "SCALP_VOL_MULT": 1.5,
                "MR_ADX_CAP": 18,
                "TREND_ADX_MIN": 25,
                "BRK_VOL_MULT": 1.2,
                "SCALP_PARTIAL_FRAC": 0.50,
                "VOL_PAUSE_RATIO": 1.5,
            },
            "HEAVY": {
                "ATR_PCT_MIN": 0.0006,
                "ATR_PCT_MAX": 0.0150,
                "VWAP_SLOPE_MAX": 0.00045,
                "SCALP_VOL_MULT": 2.0,
                "MR_ADX_CAP": 16,
                "TREND_ADX_MIN": 30,
                "BRK_VOL_MULT": 1.5,
                "SCALP_PARTIAL_FRAC": 0.60,
                "VOL_PAUSE_RATIO": 1.3,
            },
        }

        self._BASE = {
            "SPREAD_BPS_MAX": 10,
            "TP_FLOOR": 0.0020,
            "FEE_R_MAX": 0.25,
            "RISK_PCT_SCALP": 0.010,
            "RISK_PCT_H1": 0.003,
            "LEV_CAP_SCALP": 8,
            "LEV_CAP_H1": 1,
        }

        # NOTE: default auto_trade is OFF (you toggle it in the UI)
        self.settings: dict[str, Any] = {
            "scalp_mode": True,
            "auto_trade": False,          # <-- OFF by default (changed)
            "strategy": "Adaptive Router",
            "macro_pause": False,
            "profile_mode": "AUTO",
        }

        self.profile_active: str = "LIGHT"
        self.profile: dict[str, Any] = self._compose_profile(self.profile_active)

        self.router = StrategyRouter()
        self.broker = PaperBroker(start_equity=settings.start_equity)

        self._cool_until: int = 0
        self._loss_streak: int = 0

        self._heavy_locked_until: int | None = None
        self._last_trade_was_loss: bool = False
        self._last_close_ts: int | None = None
        self._entered_heavy_at: int | None = None

        self._last_wait_reason: dict[str, str | None] = {"m1": None, "h1": None}

    # --------------- helpers ----------------

    def _compose_profile(self, name: str) -> dict[str, Any]:
        p = self._PROFILES["HEAVY" if name == "HEAVY" else "LIGHT"].copy()
        p.update(self._BASE)
        return p

    def _log(self, text: str, set_status: bool = True) -> None:
        if set_status:
            self.status_text = text
        self.logs.append({"ts": int(time.time()), "text": text})
        self.logs = self.logs[-500:]

    def _rebuild_vwap(self) -> None:
        out: list[float | None] = []
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

    def _aggregate_h1(self) -> None:
        if not self.m1:
            return
        agg: dict[int, dict[str, Any]] = {}
        for c in self.m1:
            bucket = (c["time"] // 3600) * 3600
            b = agg.get(bucket)
            if not b:
                agg[bucket] = {
                    "time": bucket,
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c.get("volume", 0.0),
                }
            else:
                b["high"] = max(b["high"], c["high"])
                b["low"] = min(b["low"], c["low"])
                b["close"] = c["close"]
                b["volume"] += c.get("volume", 0.0)
        if not self.h1:
            self.h1 = sorted(agg.values(), key=lambda x: x["time"])
            return
        by_time = {bar["time"]: dict(bar) for bar in self.h1}
        for t, bar in agg.items():
            by_time[t] = bar
        self.h1 = sorted(by_time.values(), key=lambda x: x["time"])

    def _day_pnl(self) -> float:
        sod = sod_sec()
        return sum([t.pnl for t in self.broker.history if (t.close_time or t.open_time) >= sod])

    def _fills_today(self) -> int:
        sod = sod_sec()
        return sum(1 for t in self.broker.history if (t.close_time or t.open_time) >= sod) + (1 if self.broker.pos else 0)

    def _atr_pct_m1(self) -> float | None:
        if len(self.m1) < 16:
            return None
        a14 = atr(self.m1, 14)
        i = len(self.m1) - 2
        px = self.m1[i]["close"]
        return (a14[i] or 0.0) / max(1.0, px)

    def _atr_pct_m1_ratio_to_med50(self) -> float | None:
        if len(self.m1) < 65:
            return None
        a14 = atr(self.m1, 14)
        vals = []
        for k in range(len(self.m1) - 52, len(self.m1) - 2):
            px = self.m1[k]["close"]
            vals.append((a14[k] or 0.0) / max(1.0, px))
        med = median(vals) if vals else None
        cur = self._atr_pct_m1()
        if med is None or not cur:
            return None
        return cur / max(1e-8, med)

    # --------------- lifecycle ----------------

    async def start(self, client) -> None:
        """Begin polling and trading loop using the provided HTTP client."""
        self.client = client
        m1_seed, h1_seed, source = await seed_klines(client)
        self.m1 = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in m1_seed]
        self.h1 = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in h1_seed]
        self._rebuild_vwap()
        self._log(
            f"Engine initialized. Seeded m1={len(self.m1)} bars, h1={len(self.h1)} bars. "
            f"(source: {source})"
        )
        asyncio.create_task(self._run())

    def _push_trade_to_m1(self, price: float, iso: str) -> None:
        if "T" in iso:
            ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        else:
            ts = float(iso)
        t = int(ts // 60) * 60
        if not self.m1 or self.m1[-1]["time"] != t:
            self.m1.append({"time": t, "open": price, "high": price, "low": price, "close": price, "volume": 1.0})
            self.m1 = self.m1[-3000:]
        else:
            c = self.m1[-1]
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price
            c["volume"] = (c.get("volume", 0.0) or 0.0) + 1.0

    def _apply_profile(self, name: str) -> None:
        if name not in ("LIGHT", "HEAVY"):
            name = "LIGHT"
        self.profile_active = name
        self.profile = self._compose_profile(name)

    def _maybe_auto_switch(self) -> None:
        if self.settings.get("profile_mode") != "AUTO":
            self._apply_profile("HEAVY" if self.settings["profile_mode"] == "HEAVY" else "LIGHT")
            return

        now = int(time.time())
        sod = sod_sec()

        if self._heavy_locked_until and now >= self._heavy_locked_until:
            self._heavy_locked_until = None
            self._apply_profile("LIGHT")
            self._entered_heavy_at = None

        day_pnl = self._day_pnl()
        atr_ratio = self._atr_pct_m1_ratio_to_med50()
        macro_pause = bool(self.settings.get("macro_pause"))

        escalate = False
        if self._loss_streak >= 2:
            escalate = True
        if day_pnl <= -200:
            escalate = True
        if atr_ratio is not None and atr_ratio > 1.5:
            escalate = True
        if macro_pause:
            escalate = True

        if escalate and self.profile_active != "HEAVY":
            self._apply_profile("HEAVY")
            self._entered_heavy_at = now
            self._heavy_locked_until = sod + 86400
            self._log("AUTO: Escalated to HEAVY for safety (rest of UTC day).", set_status=False)

        if self.profile_active == "HEAVY" and self._entered_heavy_at:
            elapsed = now - self._entered_heavy_at
            if elapsed >= 3600:
                revert_ok = True
                if day_pnl < 0:
                    revert_ok = False
                if atr_ratio is None or atr_ratio > 1.2:
                    revert_ok = False
                recent = [t for t in self.broker.history if t.close_time >= (now - 1800)]
                if len(recent) >= 2 and (recent[-1].pnl < 0 and recent[-2].pnl < 0):
                    revert_ok = False
                if revert_ok:
                    self._apply_profile("LIGHT")
                    self._entered_heavy_at = None
                    self._log("AUTO: Reverted to LIGHT (conditions normalized).", set_status=False)

    def _friendly_status(self, *, sig_reason: str | None, tf: str, now: int) -> str:
        if not self.settings.get("auto_trade"):
            return "Off"

        p = self.broker.pos
        if p:
            when = datetime.utcfromtimestamp(p.open_time).strftime("%H:%M:%S")
            side = "Entered long" if p.side == "long" else "Entered short"
            return f"{side} @ ${p.entry:.2f} • {when} UTC"

        if now < self._cool_until:
            return f"Pausing {_mmss(self._cool_until - now)} to avoid chop"

        if self.settings.get("macro_pause"):
            return "Macro pause"

        pnl = self._day_pnl()
        fills = self._fills_today()
        if pnl <= -500 or pnl >= 500 or (tf == "m1" and fills >= 60):
            return "Day guardrail on"

        if tf == "m1":
            if len(self.m1) < 5:
                return "Loading recent prices"
            if not self.vwap or self.vwap[-2] is None:
                return "Building average line"
        else:
            if len(self.h1) < 240:
                return "Need H1 history"

        atr_ratio = self._atr_pct_m1_ratio_to_med50()
        if atr_ratio is not None:
            if self.profile_active == "LIGHT" and tf == "m1" and atr_ratio > self._PROFILES["LIGHT"]["VOL_PAUSE_RATIO"]:
                return "Volatility pause"
            if self.profile_active == "HEAVY" and atr_ratio > self._PROFILES["HEAVY"]["VOL_PAUSE_RATIO"]:
                return "Volatility pause"

        r = (sig_reason or "").lower()

        def _pct(x: float | None) -> str:
            return f"{(x or 0.0) * 100:.2f}%"

        if "atr range" in r:
            ap = self._atr_pct_m1()
            mn = self.profile.get("ATR_PCT_MIN", 0.0004)
            mx = self.profile.get("ATR_PCT_MAX", 0.02)
            if ap is None:
                return "Loading recent prices"
            return ("Movement too small (" + _pct(ap) + " < " + _pct(mn) + " min)") if ap < mn else \
                   ("Movement too wild (" + _pct(ap) + " > " + _pct(mx) + " max)")

        if "spread" in r:
            return "Spread too wide"
        if "fee" in r:
            return "Thinking fees will eat this one"
        if "pause" in r or "cooldown" in r:
            return "Pausing to avoid chop"
        if "vwap" in r or "slope" in r or "trend" in r:
            return "One-way move. Waiting pullback"

        if "inside bands" in r and tf == "m1" and self.vwap and len(self.m1) >= 2:
            i = len(self.m1) - 2
            px = self.m1[i]["close"]
            vw = self.vwap[i]
            if vw is not None:
                return "Need red close toward average" if px > vw else "Need green close toward average"

        if "waiting donchian break" in r and len(self.h1) >= 2:
            dc = donchian(self.h1, 20)
            i = len(self.h1) - 2
            hi = dc["hi"][i - 1] if i - 1 >= 0 else None
            lo = dc["lo"][i - 1] if i - 1 >= 0 else None
            px = self.h1[i]["close"]
            if hi is not None and lo is not None:
                if abs(px - hi) < abs(px - lo):
                    return "Need break above last high"
                else:
                    return "Need break below last low"

        return "Waiting for the next trade"

    async def _run(self) -> None:
        last_m1_closed = 0
        last_h1_closed = 0
        while True:
            try:
                px, bid, ask = await poll_tick(self.client)
                if bid and ask:
                    self.bid, self.ask = bid, ask
                shown = ((bid + ask) / 2.0) if (bid and ask) else (px or None)
                if shown is not None:
                    self.price = shown
                    iso = datetime.utcnow().isoformat() + "Z"
                    self._push_trade_to_m1(shown, iso)
                    self._rebuild_vwap()
                    self._aggregate_h1()

                if self.broker.pos and self.price is not None:
                    _ = self.broker.mark(self.price)
                    p = self.broker.pos
                    R = p.stop_dist

                    if p.tf == "m1":
                        if p.profile == "HEAVY" and not p.be and (int(time.time()) - p.open_time) >= p.scratch_after_sec:
                            hit_qtr = (self.price >= p.entry + 0.25 * R) if p.side == "long" else (self.price <= p.entry - 0.25 * R)
                            if not hit_qtr:
                                p.stop = p.entry
                                p.be = True

                        if not p.partial_taken:
                            hit_half = (self.price >= p.entry + 0.5 * R) if p.side == "long" else (self.price <= p.entry - 0.5 * R)
                            if hit_half:
                                frac_default = 0.60 if p.profile == "HEAVY" else 0.50
                                prof_conf = self._PROFILES.get(p.profile, {})
                                frac_conf = prof_conf.get("SCALP_PARTIAL_FRAC", frac_default)
                                try:
                                    frac = float(frac_conf)
                                except Exception:
                                    frac = frac_default
                                frac = max(0.05, min(0.90, frac))
                                self.broker.partial_close(frac, self.price)
                                if self.broker.pos:
                                    self.broker.pos.stop = self.broker.pos.entry
                                    self.broker.pos.be = True
                                    self.broker.pos.partial_taken = True

                        if self.broker.pos:
                            p = self.broker.pos
                            if p.side == "long":
                                new_stop = self.price - R
                                if new_stop > p.stop:
                                    p.stop = new_stop
                            else:
                                new_stop = self.price + R
                                if new_stop < p.stop:
                                    p.stop = new_stop

                    else:
                        if p.side == "long":
                            if (self.price >= p.entry + R) and not p.be:
                                p.stop = p.entry
                                p.be = True
                            new_stop = self.price - R
                            if new_stop > p.stop:
                                p.stop = new_stop
                        else:
                            if (self.price <= p.entry - R) and not p.be:
                                p.stop = p.entry
                                p.be = True
                            new_stop = self.price + R
                            if new_stop < p.stop:
                                p.stop = new_stop

                    p = self.broker.pos
                    if p:
                        hit_stop = (self.price <= p.stop) if p.side == "long" else (self.price >= p.stop)
                        hit_take = (self.price >= p.take) if p.side == "long" else (self.price <= p.take)
                        if hit_take or hit_stop:
                            net = self.broker.close(p.take if hit_take else self.price)
                            if hit_take:
                                self._log(f"Closed on TAKE ({p.side}); PnL {net:+.2f}")
                            else:
                                self._log(f"Closed on STOP ({p.side}); PnL {net:+.2f}")
                            if net is not None and net > 0:
                                self._loss_streak = 0
                                self._last_trade_was_loss = False
                            elif net is not None and net < 0:
                                self._loss_streak += 1
                                self._last_trade_was_loss = True
                            self._last_close_ts = int(time.time())
                            if self._loss_streak >= 3:
                                self._cool_until = int(time.time()) + 1800
                                self._log("Cooling off after 3 losses (30 min).")
                                self._loss_streak = 0

                if len(self.m1) >= 2 and self.m1[-2]["time"] != last_m1_closed:
                    last_m1_closed = self.m1[-2]["time"]
                    await self._maybe_signal(tf="m1")
                if len(self.h1) >= 2 and self.h1[-2]["time"] != last_h1_closed:
                    last_h1_closed = self.h1[-2]["time"]
                    await self._maybe_signal(tf="h1")
            except Exception as e:
                self.status_text = f"Error loop: {e}"
                self._log(f"Error in loop: {e}", set_status=False)

            self._maybe_auto_switch()
            await asyncio.sleep(1.0)

    async def _maybe_signal(self, tf: str) -> None:
        now = int(time.time())

        if self.settings.get("macro_pause"):
            self.status_text = "Macro pause"
            return
        if now < self._cool_until:
            self.status_text = f"Pausing {_mmss(self._cool_until - now)} to avoid chop"
            return

        pnl = self._day_pnl()
        fills = self._fills_today()

        pnl_ok = (-500 < pnl < 500)
        if not pnl_ok:
            self.status_text = "Day guardrail on"
            return

        scalp = bool(self.settings.get("scalp_mode"))
        src = self.m1 if scalp else self.h1
        iC = (len(src) - 2) if len(src) >= 2 else None

        iC_h1 = (len(self.h1) - 2) if len(self.h1) >= 2 else None
        iC_m1 = (len(self.m1) - 2) if len(self.m1) >= 2 else None

        atr_ratio = self._atr_pct_m1_ratio_to_med50()
        if atr_ratio is not None:
            if self.profile_active == "LIGHT" and scalp and atr_ratio > self._PROFILES["LIGHT"]["VOL_PAUSE_RATIO"]:
                self.status_text = "Volatility pause"
                return
            if self.profile_active == "HEAVY" and atr_ratio > self._PROFILES["HEAVY"]["VOL_PAUSE_RATIO"]:
                self.status_text = "Volatility pause"
                return

        last_open = self.broker.history[-1].open_time if self.broker.history else 0
        cooldown_ok_ctx = (now - last_open) > (60 if scalp else 3600)

        context = {
            "daily_ok": (pnl_ok and (fills < 60 if scalp else True)),
            "cooldown_ok": cooldown_ok_ctx,
            "fills": fills,
            "max_fills": 60,
            "fee_rate": FEE_MAKER,
            "fee_taker": FEE_TAKER,
            "profile": {
                "ATR_PCT_MIN": self.profile["ATR_PCT_MIN"],
                "ATR_PCT_MAX": self.profile["ATR_PCT_MAX"],
                "VWAP_SLOPE_MAX": self.profile["VWAP_SLOPE_MAX"],
                "SPREAD_BPS_MAX": self.profile["SPREAD_BPS_MAX"],
                "TP_FLOOR": self.profile["TP_FLOOR"],
                "FEE_R_MAX": self.profile["FEE_R_MAX"],
                "SCALP_VOL_MULT": self.profile["SCALP_VOL_MULT"],
            },
            "adx_trend_min": self.profile["TREND_ADX_MIN"],
            "adx_range_max": self.profile["MR_ADX_CAP"],
            "breakout_vol_mult": self.profile["BRK_VOL_MULT"],
            "vwap": self.vwap,
            "bid": self.bid,
            "ask": self.ask,
            "m1": self.m1,
            "h1": self.h1,
            "iC": iC,
            "iC_m1": iC_m1,
            "iC_h1": iC_h1,
            "min_bars": 5,
            "min_h1_bars": 240,
            "profile_mode_active": self.profile_active,
            "scalp_mode": scalp,
            "macro_pause": self.settings.get("macro_pause"),
        }

        strategy_router = self.router.pick(scalp)
        sig = strategy_router.evaluate(src, context)

        self.status_text = self._friendly_status(
            sig_reason=getattr(sig, "reason", None),
            tf=("m1" if scalp else "h1"),
            now=now,
        )

        if sig.type == "WAIT":
            key = "m1" if scalp else "h1"
            prev = self._last_wait_reason.get(key)
            if sig.reason != prev:
                text = f"WAIT[{key}]: {sig.reason}"
                if "ATR range" in (sig.reason or ""):
                    ap = self._atr_pct_m1() or 0.0
                    mn = self.profile.get("ATR_PCT_MIN", 0.0004)
                    mx = self.profile.get("ATR_PCT_MAX", 0.0200)
                    text += f" (ATR% {ap*100:.2f}% in band {mn*100:.2f}–{mx*100:.2f}%)"
                elif "Inside bands" in (sig.reason or "") and key == "m1" and len(self.m1) >= 3 and self.vwap:
                    i = len(self.m1) - 2
                    prev_bar = self.m1[i - 1] if i - 1 >= 0 else None
                    vprev = self.vwap[i - 1] if i - 1 >= 0 else None
                    atr_pct = self._atr_pct_m1() or 0.0
                    tp_floor = self.profile.get("TP_FLOOR", 0.0020)
                    band_pct = max(tp_floor, 0.7 * atr_pct)
                    if prev_bar and vprev:
                        dlow = max(0.0, (vprev - prev_bar["low"]) / max(1e-12, vprev))
                        dhigh = max(0.0, (prev_bar["high"] - vprev) / max(1e-12, vprev))
                        text += f" (need ≥{band_pct*100:.2f}% overshoot; prev LΔ={dlow*100:.2f}%, HΔ={dhigh*100:.2f}%)"
                self._last_wait_reason[key] = sig.reason
                self._log(text, set_status=False)

        if sig.type in ("BUY", "SELL") and self.settings.get("auto_trade") and self.price is not None:
            tf_from_sig = getattr(sig, "tf", None)
            if tf_from_sig in ("m1", "h1"):
                trade_tf = tf_from_sig
                is_h1 = (trade_tf == "h1")
            else:
                active_name = strategy_router.last_strategy or ""
                active_name_norm = _normalize_hyphens(active_name)
                is_h1 = active_name_norm in {"Mean Reversion (H1)", "Breakout", "Trend-Following"}
                trade_tf = "h1" if is_h1 else "m1"

            last_open = self.broker.history[-1].open_time if self.broker.history else 0
            cooldown_ok = (now - last_open) > (3600 if trade_tf == "h1" else 60)
            if not cooldown_ok:
                return

            series = self.h1 if is_h1 else self.m1
            i_entry = (len(series) - 2)
            if i_entry is None or i_entry < 0 or i_entry >= len(series):
                return
            entry = series[i_entry]["close"]

            stopd = sig.stop_dist or (entry * 0.005)
            taked = sig.take_dist or (entry * 0.005)

            # --- SIZING: FULL PAPER EQUITY NOTIONAL (no risk model) ---
            # Use the entire paper equity as the notional for every trade so the UI's
            # account balance (e.g., $10,000) is exactly the trade size.
            notional = max(0.0, self.broker.equity)
            qty = max(0.0001, notional / max(1.0, entry))

            stop = (entry - stopd) if sig.type == "BUY" else (entry + stopd)
            take = (entry + taked) if sig.type == "BUY" else (entry - taked)

            if self.broker.pos:
                ps = self.broker.pos.side
                if (sig.type == "BUY" and ps == "short") or (sig.type == "SELL" and ps == "long"):
                    self.broker.close(entry)
                    self._log(f"Reversed position at {entry:.2f}")

            if not self.broker.pos:
                self.broker.open(
                    sig.type, entry, qty, stop, take, stopd, maker=True,
                    tf=trade_tf,
                    profile=self.profile_active,
                    scratch_after_sec=300,
                )
                self._log(
                    f"Open {sig.type} @ {entry:.2f} | qty={qty:.6f} notional≈${notional:.2f} stop={stop:.2f} take={take:.2f} score={sig.score}",
                    set_status=False,
                )
