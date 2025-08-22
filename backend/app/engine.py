"""Core trading engine — Strategy V3 (Dynamic VS/PS, no modes).

Highlights:
- Removes LIGHT/HEAVY/AUTO + 1m/1h UI modes entirely.
- Implements VS (volatility score) and PS (performance score).
- m1 scalps + selective h1 swings via RouterV3.
- Giveback stop, day guard, streak governance, cooldowns.
- Slippage/depth gating using synthetic top-3 notional until real depth is wired.
- Fallback loosening once/day when H1 is dead but tradeable.
- Short, human STATUS messages (2–4 words).
"""

from __future__ import annotations

import asyncio, time, math
from datetime import datetime
from statistics import median
from typing import Any, Optional

from .config import settings
from .datafeed import seed_klines, poll_tick
from .broker import PaperBroker, FEE_MAKER
from .models import Position, Trade
from .strategies.base import Signal   # FIX: was missing before
from .strategies.router import RouterV3
from .ta import atr, rsi, macd_line_signal, adx


def sod_sec() -> int:
    return int((int(time.time()) // 86400) * 86400)


def _clamp(a: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, a))


def _pct(x: float) -> float:
    return x * 100.0


class BotEngine:
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

        # Indicators cache
        self._rsi_m1: list[Optional[float]] = []
        self._rsi_h1: list[Optional[float]] = []
        self._macd_m1: tuple[list[Optional[float]], list[Optional[float]]] = ([], [])
        self._macd_h1: tuple[list[Optional[float]], list[Optional[float]]] = ([], [])

        # VS/PS & session
        self.VS: float = 1.0
        self.PS: float = 0.5
        self._loss_streak: float = 0.0
        self._losses_today: int = 0
        self._require_macd_next_m1: bool = False
        self._risk_downscale_next: bool = False

        self._day_open_equity: float = settings.start_equity
        self._day_high_equity: float = settings.start_equity
        self._pause_until: int = 0                 # giveback / streak cooldown
        self._macro_until: int = 0                 # macro pause auto‑off

        self._last_open_m1: int = 0
        self._last_open_h1: int = 0
        self._last_trade_ts: int = 0              # for PS idle decay
        self._last_ps_decay: int = 0              # throttle PS decay steps
        self._last_decision: dict[str, Any] = {}

        # slippage/depth telemetry (approx until depth is wired)
        self._last_spread_bps: Optional[float] = None
        self._last_fee_to_tp: Optional[float] = None
        self._last_slip_est: Optional[float] = None
        self._synthetic_top3_notional: float = settings.synthetic_top3_notional

        # realized R track
        self._last_Rs: list[float] = []

        # Fallback loosening (once/day)
        self._fallback_pending_m1: bool = False
        self._fallback_used_day: Optional[int] = None

        # Router
        self.router = RouterV3()

        # Paper broker
        self.broker = PaperBroker(start_equity=settings.start_equity)

        # Controls
        self.settings: dict[str, Any] = {
            "auto_trade": False,
            "macro_pause": False,
        }

        self._last_wait_reason: Optional[str] = None

    # ---------------- helpers ----------------

    def _log(self, text: str, set_status: bool = False) -> None:
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

    def _warm_ok(self) -> bool:
        return len(self.m1) >= 5 and len(self.h1) >= 220

    def _day_pnl(self) -> float:
        sod = sod_sec()
        return sum([t.pnl for t in self.broker.history if (t.close_time or t.open_time) >= sod])

    def _day_pnl_pct(self) -> float:
        cur = self.broker.equity
        run = (cur - self._day_open_equity)
        return (_pct(run / max(1e-9, self._day_open_equity)))

    def _fills_today(self) -> int:
        sod = sod_sec()
        return sum(1 for t in self.broker.history if (t.close_time or t.open_time) >= sod) + (1 if self.broker.pos else 0)

    def _atr_pct_m1(self) -> Optional[float]:
        if len(self.m1) < 16:
            return None
        a14 = atr(self.m1, 14)
        i = len(self.m1) - 2
        px = self.m1[i]["close"]
        return (a14[i] or 0.0) / max(1.0, px)

    def _atr_ratio_vs_median50(self) -> Optional[float]:
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
        return cur / max(1e-9, med)

    def _update_indicators(self) -> None:
        closes_m1 = [c["close"] for c in self.m1]
        closes_h1 = [c["close"] for c in self.h1]
        self._rsi_m1 = rsi(closes_m1, 14)
        self._rsi_h1 = rsi(closes_h1, 14)
        self._macd_m1 = macd_line_signal(closes_m1, 12, 26, 9)
        self._macd_h1 = macd_line_signal(closes_h1, 12, 26, 9)

    def _update_VS_PS(self, now: Optional[int] = None) -> None:
        # VS = clamp( m1_ATR% / median(m1_ATR%, last 50), 0.5, 2.0 )
        atr_ratio = self._atr_ratio_vs_median50()
        self.VS = _clamp(atr_ratio, 0.5, 2.0) if atr_ratio is not None else 1.0

        # PS = clamp(0..1, 0.5 + 0.10 * PnL_day_% − 0.15 * loss_streak)
        day_pct = self._day_pnl_pct()
        PS = 0.5 + 0.10 * day_pct - 0.15 * max(0.0, self._loss_streak)
        self.PS = _clamp(PS, 0.0, 1.0)

        # Idle decay toward 0.5 if flat for >= 2h (apply every 10 min)
        if now is None:
            now = int(time.time())
        if self._last_trade_ts and (now - self._last_trade_ts) >= 7200:
            if (now - self._last_ps_decay) >= 600:
                self.PS += (0.5 - self.PS) * 0.10
                self._last_ps_decay = now

    def _short_status(self, key: str) -> str:
        """Map reasons to very short statuses (2–4 words)."""
        m = {
            "off": "Off",
            "macro": "Macro pause",
            "cool": "Cooling off",
            "waiting": "Waiting setup",
            "vol_low": "Too quiet",
            "vol_high": "Too wild",
            "spread": "Spread too wide",
            "fees": "Fees too high",
            "pullback": "Waiting pullback",
            "managing": "Managing trade",
            "trailing": "Trailing stop",
            "partial": "Taking partials",
            "scratch": "Scratching trade",
            "giveback": "Protecting day",
            "trend": "Trend play",
            "range": "Range fade",
            "break": "Breakout watch",
        }
        return m.get(key, "Standing by")

    # ---------------- lifecycle ----------------

    async def start(self, client) -> None:
        self.client = client
        m1_seed, h1_seed, source = await seed_klines(client)
        self.m1 = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in m1_seed]
        self.h1 = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in h1_seed]
        self._rebuild_vwap()
        self._update_indicators()
        self._day_open_equity = self.broker.equity
        self._day_high_equity = self.broker.equity
        self._log(f"Engine ready. Seeded m1={len(self.m1)} h1={len(self.h1)} (src: {source})", set_status=True)
        asyncio.create_task(self._run())

    def _push_m1(self, price: float, iso: str) -> None:
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

    async def _run(self) -> None:
        last_m1_closed = 0
        last_h1_closed = 0

        while True:
            # poll
            try:
                px, bid, ask = await poll_tick(self.client)
                if (bid is not None) and (ask is not None):
                    self.bid, self.ask = bid, ask
                    self._last_spread_bps = ((ask - bid) / max(1e-9, (bid + ask) / 2.0)) * 10000.0
                shown = ((bid + ask) / 2.0) if ((bid is not None) and (ask is not None)) else (px if px is not None else None)
                if shown is not None:
                    self.price = shown
                    iso = datetime.utcnow().isoformat() + "Z"
                    self._push_m1(shown, iso)
                    self._rebuild_vwap()
                    self._aggregate_h1()
                    self._update_indicators()
            except Exception as e:
                self._log(f"Data error: {e}")

            # VS/PS, macro/giveback
            now = int(time.time())
            self._update_VS_PS(now)

            # Macro pause auto-off
            if self._macro_until and now >= self._macro_until:
                self._macro_until = 0
                self.settings["macro_pause"] = False

            # Giveback stop
            if self.broker.equity > self._day_high_equity:
                self._day_high_equity = self.broker.equity
            runup = self._day_high_equity - self._day_open_equity
            giveback = self._day_high_equity - self.broker.equity
            gb_limit = 0.35
            if self.VS >= 1.5 and self.PS <= 0.4:
                gb_limit = 0.30
            if runup > 0 and giveback >= gb_limit * runup and self._pause_until < now + 1800:
                self._pause_until = now + 1800  # 30m
                self._log("Giveback guard: pausing 30m", set_status=False)

            # Macro pause via ATR spike
            atr_ratio = self._atr_ratio_vs_median50()
            if atr_ratio is not None and atr_ratio > 1.8 and not self.settings.get("macro_pause"):
                self.settings["macro_pause"] = True
                self._macro_until = now + 1800  # 30m
                self._log("Macro pause: volatility spike", set_status=False)

            # Manage position
            if self.broker.pos and self.price is not None:
                self._manage_position(now)

            # On closed bars, schedule signals: m1 first if VS≥1 else h1 first
            m1_closed = (len(self.m1) >= 2 and self.m1[-2]["time"] != last_m1_closed)
            h1_closed = (len(self.h1) >= 2 and self.h1[-2]["time"] != last_h1_closed)
            if m1_closed or h1_closed:
                if m1_closed:
                    last_m1_closed = self.m1[-2]["time"]
                if h1_closed:
                    last_h1_closed = self.h1[-2]["time"]
                await self._maybe_decide(now)

            # STATUS (short & human)
            self._status_tick(now)

            await asyncio.sleep(0.5)

    # --------------- decision & sizing ----------------

    def _maybe_set_fallback(self, now: int) -> None:
        """Arm fallback loosening for next m1 entry once per UTC day when H1 is quiet but tradable."""
        # already used today?
        if self._fallback_used_day == sod_sec():
            return
        # last H1 open >= 4h ago?
        four_hours = 4 * 3600
        if (now - self._last_open_h1) < four_hours:
            return
        # ADX window guard: 15 <= ADX(h1) <= 22
        iH = len(self.h1) - 2 if len(self.h1) >= 2 else None
        if iH is None or iH < 1:
            return
        ax = adx(self.h1, 14)
        a = (ax[iH] or 0.0)
        if 15.0 <= a <= 22.0:
            self._fallback_pending_m1 = True

    async def _maybe_decide(self, now: int) -> None:
        if not self.settings.get("auto_trade"):
            return
        if not self._warm_ok():
            self.status_text = self._short_status("waiting")
            return

        if self.settings.get("macro_pause"):
            self.status_text = self._short_status("macro")
            return
        if now < self._pause_until:
            self.status_text = self._short_status("cool")
            return

        # Hard day guard
        if self._day_pnl_pct() <= -5.0 or self._losses_today >= 4:
            self.status_text = self._short_status("giveback")
            return

        # Try arming fallback loosening if conditions stand
        self._maybe_set_fallback(now)

        # Cooldowns
        cooldown_ok_m1 = (now - self._last_open_m1) > 45
        cooldown_ok_h1 = (now - self._last_open_h1) > 1800

        # Router context
        iC_m1 = len(self.m1) - 2 if len(self.m1) >= 2 else None
        iC_h1 = len(self.h1) - 2 if len(self.h1) >= 2 else None
        ctx = {
            "m1": self.m1, "h1": self.h1,
            "iC_m1": iC_m1, "iC_h1": iC_h1,
            "vwap": self.vwap,
            "bid": self.bid, "ask": self.ask,
            "min_bars": 5, "min_h1_bars": 220,
            "VS": self.VS, "PS": self.PS,
            "loss_streak": self._loss_streak,
        }

        # VS-driven scheduling: m1 first if VS≥1, else h1 first
        order = ("m1", "h1") if self.VS >= 1.0 else ("h1", "m1")

        sig: Optional[Signal] = None
        for _ in order:
            s = self.router.evaluate(ctx)
            # Apply extra governance on m1 after 3 losses
            if s.tf == "m1" and self._require_macd_next_m1 and s.type in ("BUY", "SELL"):
                closes_m1 = [c["close"] for c in self.m1]
                mline, msignal = macd_line_signal(closes_m1, 12, 26, 9)
                idx = iC_m1
                recent = []
                for k in range(0, 4):
                    j = (idx - k) if idx is not None and idx - k >= 1 else None
                    if j is None: break
                    prev = (mline[j - 1] or 0.0) - (msignal[j - 1] or 0.0)
                    cur = (mline[j] or 0.0) - (msignal[j] or 0.0)
                    recent.append((prev, cur))
                ok = any((p <= 0 < c) or (p >= 0 > c) for (p, c) in recent)
                if not ok:
                    s = Signal(type="WAIT", reason="Require MACD confirm")
            # enforce cooldowns by TF
            if s.tf == "m1" and not cooldown_ok_m1:
                s = Signal(type="WAIT", reason="m1 cooldown")
            if s.tf == "h1" and not cooldown_ok_h1:
                s = Signal(type="WAIT", reason="h1 cooldown")

            sig = s
            if s.type != "WAIT":
                break

        # Telemetry for UI
        self._last_decision = {
            "regime": self.router.last_regime,
            "bias": self.router.last_bias,
            "adx": self.router.last_adx,
            "atrPct": self.router.last_atr_pct,
            "active": self.router.last_strategy if sig and sig.type != "WAIT" else None,
        }

        if sig is None or sig.type == "WAIT":
            self._last_wait_reason = sig.reason if sig else "—"
            return

        # Sizing & risk
        if self.price is None or sig.stop_dist is None or sig.take_dist is None:
            return

        equity = max(1e-9, float(self.broker.equity))
        base_risk = 0.008 if sig.tf == "m1" else 0.0025
        atr_pct = self._atr_pct_m1() or 0.0

        # Fallback loosening: once/day, m1 only
        VS_eff = self.VS
        if sig.tf == "m1" and self._fallback_pending_m1:
            VS_eff = max(0.9, min(2.0, VS_eff + 0.2))
        atr_norm = min(1.0, atr_pct / max(1e-9, (0.0175 * VS_eff)))
        risk_mult = 1.0 - 0.40 * atr_norm
        eff = base_risk * risk_mult * self.PS
        if self.PS < 0.5:
            eff = min(eff, 0.8 * base_risk)
        if self._risk_downscale_next:
            eff *= 0.5
            self._risk_downscale_next = False

        # Uplift (m1)
        if sig.tf == "m1" and self.PS >= 0.70 and 0.8 <= self.VS <= 1.4:
            eff *= 1.30
            last5 = self._last_Rs[-5:]
            # only tier-2 uplift if: rolling E[R] ≥ 0.25R, no giveback pause triggered, spread ≤4 bps AND slip ok
            tier2_ok = (len(last5) >= 3 and (sum(last5) / len(last5)) >= 0.25 and (self._pause_until == 0 or self._pause_until < int(time.time())))
            if tier2_ok and (self._last_spread_bps or 0) <= 4.0:
                # slip check later after we compute it
                eff = min(eff * (50.0 / 30.0), 1.5 * base_risk)

        stopd = sig.stop_dist
        qty = (equity * eff) / max(1e-9, stopd)

        # Synthetic top-3 depth and slippage estimate
        entry_price = self.price
        order_notional = qty * entry_price
        top3_notional = self._synthetic_top3_notional
        top3_qty = top3_notional / max(1e-9, entry_price)

        # Maker-only in paper: slip = spread/2; if bid/ask missing, assume 6 bps total spread conservatively
        if (self.bid is not None) and (self.ask is not None):
            spread_abs = self.ask - self.bid
        else:
            spread_abs = entry_price * 0.0006  # 6 bps fallback
        slip_est = spread_abs / 2.0
        self._last_slip_est = slip_est

        # leverage cap (10× only when spread tight AND slip <= 0.3R; h1 stays at 2×)
        lev_cap = 10.0 if (sig.tf == "m1" and (self._last_spread_bps or 999) <= 4.0 and slip_est <= 0.3 * stopd) else (2.0 if sig.tf == "h1" else 5.0)
        qty_cap = (equity * lev_cap) / max(1.0, entry_price)
        qty = max(0.0, min(qty, qty_cap))

        # Combined live risk cap (single position in this engine)
        if self.broker.pos:
            live = (self.broker.pos.stop_dist * self.broker.pos.qty) / equity
            if (live + eff) > 0.015:
                self._last_wait_reason = "Live risk cap"
                return

        # Spread/fee/TP checks
        tp_pct = max(1e-9, sig.take_dist / max(1.0, entry_price))
        fee_to_tp = (2 * FEE_MAKER) / tp_pct
        self._last_fee_to_tp = fee_to_tp
        if fee_to_tp > 0.20:
            self._last_wait_reason = "Fees>TP"
            return
        if (self._last_spread_bps or 0) > 8.0 and sig.tf == "m1":
            self._last_wait_reason = "Spread cap"
            return

        # Slippage & synthetic depth gating (Spec §7)
        if slip_est > 0.3 * stopd:
            self._last_wait_reason = "Slip>0.3R"
            return
        if top3_notional < 2.0 * order_notional:
            self._last_wait_reason = "Depth<2x order"
            return

        # Place order (post‑only assumed; crossing logic omitted in paper)
        entry = entry_price
        stop = entry - stopd if sig.type == "BUY" else entry + stopd
        take = entry + sig.take_dist if sig.type == "BUY" else entry - sig.take_dist

        # Close & reverse if opposite
        if self.broker.pos:
            ps = self.broker.pos.side
            if (sig.type == "BUY" and ps == "short") or (sig.type == "SELL" and ps == "long"):
                self.broker.close(entry)
                self._log("Reversed position", set_status=False)

        if not self.broker.pos and qty > 0.0:
            self.broker.open(
                sig.type, entry, qty, stop, take, stopd,
                maker=True, tf=sig.tf or "m1", scratch_after_sec=240, opened_by=self._last_decision.get("active")
            )
            if sig.tf == "m1":
                self._last_open_m1 = now
                if self._fallback_pending_m1:
                    self._fallback_pending_m1 = False
                    self._fallback_used_day = sod_sec()
            else:
                self._last_open_h1 = now
            self._log(
                f"Open {sig.type} {sig.tf} @ {entry:.2f} qty={qty:.6f} R=${stopd:.2f} score={sig.score} "
                f"(spread={self._last_spread_bps or 0:.2f}bps slip_est=${slip_est:.2f} top3=${top3_notional:,.0f})",
                set_status=False
            )

    # --------------- management ----------------

    def _manage_position(self, now: int) -> None:
        _ = self.broker.mark(self.price or 0.0)
        p = self.broker.pos
        if not p:
            return
        R = p.stop_dist

        # Partial at +0.5R (m1 60% if VS<1 else 50%; h1 25–33%)
        hit_half = (self.price >= p.entry + 0.5 * R) if p.side == "long" else (self.price <= p.entry - 0.5 * R)
        if hit_half and not p.partial_taken:
            if p.tf == "m1":
                frac = 0.60 if self.VS < 1.0 else 0.50
            else:
                frac = 0.30
            self.broker.partial_close(frac, self.price)
            if self.broker.pos:
                self.broker.pos.stop = self.broker.pos.entry + (0.1 * R if p.side == "long" else -0.1 * R)
                self.broker.pos.be = True
                self.broker.pos.partial_taken = True
            self.status_text = self._short_status("partial")

        # RSI extreme extra scale‑out 25%
        if self.broker.pos and not self.broker.pos.extra_scaled:
            if p.tf == "m1":
                idx = len(self.m1) - 2
                rsi_now = self._rsi_m1[idx] if idx is not None and idx < len(self._rsi_m1) else None
            else:
                idx = len(self.h1) - 2
                rsi_now = self._rsi_h1[idx] if idx is not None and idx < len(self._rsi_h1) else None
            if rsi_now is not None:
                if (p.side == "long" and rsi_now > 80.0) or (p.side == "short" and rsi_now < 20.0):
                    self.broker.partial_close(0.25, self.price)
                    if self.broker.pos:
                        self.broker.pos.extra_scaled = True

        # Trail: 0.8R×VS (tighten 0.6R×VS on MACD fade/div)
        if self.broker.pos:
            tighten = False
            if p.tf == "m1":
                line, sig = self._macd_m1
                i = len(self.m1) - 2
            else:
                line, sig = self._macd_h1
                i = len(self.h1) - 2
            if i is not None and i > 1 and i < len(line):
                cur = (line[i] or 0.0) - (sig[i] or 0.0)
                prev = (line[i - 1] or 0.0) - (sig[i - 1] or 0.0)
                if (p.side == "long" and cur < prev) or (p.side == "short" and cur > prev):
                    tighten = True
            k = (0.6 if tighten else 0.8) * max(0.6, min(1.5, self.VS))
            if p.side == "long":
                new_stop = self.price - (k * R)
                if new_stop > p.stop:
                    p.stop = new_stop
            else:
                new_stop = self.price + (k * R)
                if new_stop < p.stop:
                    p.stop = new_stop
            self.status_text = self._short_status("trailing")

        # Time‑scratch (m1, VS<1): +0.25R not in 4 min → BE
        if p.tf == "m1" and self.VS < 1.0 and not p.be and (now - p.open_time) >= 240:
            hit_qtr = (self.price >= p.entry + 0.25 * R) if p.side == "long" else (self.price <= p.entry - 0.25 * R)
            if not hit_qtr:
                p.stop = p.entry
                p.be = True
                self.status_text = self._short_status("scratch")

        # Exit on stop/take
        hit_stop = (self.price <= p.stop) if p.side == "long" else (self.price >= p.stop)
        hit_take = (self.price >= p.take) if p.side == "long" else (self.price <= p.take)
        if hit_stop or hit_take:
            base_R_usd = p.qty * p.stop_dist
            net = self.broker.close(p.take if hit_take else self.price)
            r_mult = (net / max(1e-9, base_R_usd)) if net is not None else 0.0
            self._last_Rs.append(r_mult); self._last_Rs = self._last_Rs[-20:]
            self._last_trade_ts = now
            self._log(f"Close {'TAKE' if hit_take else 'STOP'} {p.tf} PnL {net:+.2f} ({r_mult:+.2f}R)", set_status=False)

            if net is not None and net > 0:
                self._loss_streak = 0.0
                self._require_macd_next_m1 = False
            elif net is not None and abs(net) < max(1.0, 0.02 * base_R_usd) and p.be:
                # scratch → relief
                self._loss_streak = max(0.0, self._loss_streak - 0.5)
            else:
                self._loss_streak += 1.0
                self._losses_today += 1
                if self._loss_streak >= 4.0:
                    self._pause_until = sod_sec() + 86400  # end day
                elif self._loss_streak >= 3.0:
                    self._pause_until = int(time.time()) + 2700  # 45m
                    self._require_macd_next_m1 = True
                elif self._loss_streak >= 2.0:
                    self._pause_until = int(time.time()) + 900   # 15m
                    self._risk_downscale_next = True

    # --------------- status ----------------

    def _status_tick(self, now: int) -> None:
        if not self.settings.get("auto_trade"):
            self.status_text = self._short_status("off"); return
        if self.settings.get("macro_pause"):
            self.status_text = self._short_status("macro"); return
        if now < self._pause_until:
            self.status_text = self._short_status("cool"); return
        if self.broker.pos:
            self.status_text = self._short_status("managing"); return

        # simple mapping from last wait reason → short status
        r = (self._last_wait_reason or "").lower()
        if "atr" in r or "quiet" in r:
            self.status_text = self._short_status("vol_low")
        elif "wild" in r or "macro" in r:
            self.status_text = self._short_status("vol_high")
        elif "spread" in r:
            self.status_text = self._short_status("spread")
        elif "fee" in r:
            self.status_text = self._short_status("fees")
        elif "break" in r:
            self.status_text = self._short_status("break")
        elif "trend" in r:
            self.status_text = self._short_status("trend")
        elif "mean" in r or "bands" in r or "pullback" in r:
            self.status_text = self._short_status("pullback")
        else:
            self.status_text = self._short_status("waiting")
