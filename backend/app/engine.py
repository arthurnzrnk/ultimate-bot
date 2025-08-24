"""Core trading engine — Strategy V3.4.

Key fixes:
- Hard day stop: −4% or 4 losses ⇒ pause until next UTC day.
- Re‑entry: enforce micro‑triad + (top‑hour OR z‑VWAP confirm) at half‑risk.
- Fallback loosening: allow second activation only if prior fallback window ≥ 0 PnL.
- Guarded quotes: require BBO present for new entries (reject otherwise).
- Exit types wired to broker to compute per‑side fees correctly.
"""

from __future__ import annotations
import asyncio, time, math
from datetime import datetime
from statistics import median, pstdev
from typing import Any, Optional, Deque, Tuple
from collections import deque

from .config import settings
from .datafeed import seed_klines, poll_tick
from .broker import PaperBroker
from .models import Position, Trade
from .strategies.base import Signal
from .strategies.router import RouterV3
from .ta import atr, rsi, macd_line_signal, adx, ema


def sod_sec() -> int:
    return int((int(time.time()) // 86400) * 86400)


def _clamp(a: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, a))


def _pct(x: float) -> float:
    return x * 100.0


def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return x
    return round(x / tick) * tick


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

        self._day_sod: int = sod_sec()
        self._day_open_equity: float = settings.start_equity
        self._day_high_equity: float = settings.start_equity
        self._giveback_triggered_today: bool = False

        # Day-lock
        self._day_lock_armed: bool = False
        self._day_lock_peak_equity: float = settings.start_equity
        self._day_lock_floor_pct: float | None = None

        # Pauses & breakers
        self._pause_until: int = 0
        self._macro_until: int = 0
        self._fee_violation_events: Deque[int] = deque(maxlen=10)

        # Taker throttle
        self._taker_fail_events: Deque[int] = deque(maxlen=10)
        self._fast_tape_disabled_until: int = 0

        # Heartbeat
        self._last_tick_ts: int = 0

        # Spread stability
        self._spread_bps_window: Deque[float] = deque(maxlen=90)
        self._spread_std_10s: float | None = None
        self._spread_median_60s: float | None = None

        # top3 crumble tracking
        self._top3_hist: Deque[tuple[int, float]] = deque(maxlen=120)
        self._top3_notional_drop_3s: float = 0.0

        # recent execution telemetry
        self._last_spread_bps: Optional[float] = None
        self._last_fee_to_tp: Optional[float] = None
        self._last_slip_est: Optional[float] = None
        self._synthetic_top3_notional: float = settings.synthetic_top3_notional

        # realized R window
        self._last_Rs: list[float] = []

        # H1 signals & fallback
        self._last_h1_signal_ts: int = 0
        self._fallback_pending_m1: bool = False
        self._fallback_activations_today: int = 0  # ≤ FALLBACK_MAX_ACTIVATIONS_PER_UTC
        self._fallback_session_open: bool = False
        self._fallback_session_start_equity: float = 0.0
        self._fallback_prev_nonneg: bool = False  # prior fallback window PnL ≥ 0?

        # Router
        self.router = RouterV3()

        # Broker
        self.broker = PaperBroker(start_equity=settings.start_equity)

        # Controls
        self.settings: dict[str, Any] = {"auto_trade": False, "macro_pause": False}

        # cooldown tracking
        self._last_open_m1: int = 0
        self._last_open_h1: int = 0
        self._lost_in_hour: dict[int, bool] = {}

        # Latencies (placeholders)
        self._tick_latency_ms_p95: float | None = None
        self._order_ack_p95_ms: float | None = None  # not applicable in paper
        self._latency_hits_tick: Deque[int] = deque(maxlen=10)

        # 7‑day DD monitor
        self._equity_marks: Deque[tuple[int, float]] = deque(maxlen=20000)
        self._dd7_halt: bool = False

        # Re-entry state (m1)
        self._reentry_until_ts: int = 0
        self._reentry_required_top_hour_or_zvwap: bool = False

    # --------- utils ---------

    def _log(self, text: str, set_status: bool = False) -> None:
        if set_status:
            self.status_text = text
        self.logs.append({"ts": int(time.time()), "text": text})
        self.logs = self.logs[-600:]

    def _rebuild_vwap(self) -> None:
        out: list[float | None] = []
        day = None
        pv = 0.0
        vv = 0.0
        for c in self.m1:
            d = datetime.utcfromtimestamp(c["time"]).strftime("%Y-%m-%d")
            if day != d:
                day = d; pv = 0.0; vv = 0.0
            tp = (c["high"] + c["low"] + c["close"]) / 3.0
            v = max(1e-8, c.get("volume", 0.0))
            pv += tp * v; vv += v
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
                    "time": bucket, "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
                    "volume": c.get("volume", 0.0),
                }
            else:
                b["high"] = max(b["high"], c["high"])
                b["low"] = min(b["low"], c["low"])
                b["close"] = c["close"]
                b["volume"] += c.get("volume", 0.0)
        if not self.h1:
            self.h1 = sorted(agg.values(), key=lambda x: x["time"]); return
        by_time = {bar["time"]: dict(bar) for bar in self.h1}
        for t, bar in agg.items(): by_time[t] = bar
        self.h1 = sorted(by_time.values(), key=lambda x: x["time"])

    def _warm_ok(self) -> bool:
        return len(self.m1) >= 5 and len(self.h1) >= 220

    def _day_pnl(self) -> float:
        sod = self._day_sod
        return sum([t.pnl for t in self.broker.history if (t.close_time or t.open_time) >= sod])

    def _day_pnl_pct(self) -> float:
        run = (self.broker.equity - self._day_open_equity)
        return _pct(run / max(1e-9, self._day_open_equity))

    def _fills_today(self) -> int:
        sod = self._day_sod
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
        atr_ratio = self._atr_ratio_vs_median50()
        self.VS = _clamp(atr_ratio, settings.spec.VS_MIN, settings.spec.VS_MAX) if atr_ratio is not None else 1.0
        day_pct = self._day_pnl_pct()
        PS = 0.5 + 0.10 * day_pct - 0.15 * max(0.0, self._loss_streak)
        self.PS = _clamp(PS, 0.0, 1.0)
        if now is None:
            now = int(time.time())
        # idle decay (best-effort)
        if self._fills_today() == 0 and (now - self._day_sod) >= settings.spec.PS_DECAY_HOURS_IF_IDLE * 3600:
            self.PS += (0.5 - self.PS) * 0.10

    def _short_status(self, key: str) -> str:
        m = {
            "off": "Off", "macro": "Macro pause", "cool": "Cooling off", "waiting": "Waiting setup",
            "vol_low": "Too quiet", "vol_high": "Too wild", "spread": "Spread too wide", "fees": "Fees too high",
            "managing": "Managing trade", "trailing": "Trailing stop",
            "partial": "Taking partials", "scratch": "Scratching trade", "giveback": "Protecting day",
            "trend": "Trend play", "break": "Breakout watch",
        }
        return m.get(key, "Standing by")

    # -------- lifecycle --------

    async def start(self, client) -> None:
        self.client = client
        m1_seed, h1_seed, source = await seed_klines(client)
        self.m1 = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in m1_seed]
        self.h1 = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in h1_seed]
        self._rebuild_vwap()
        self._update_indicators()
        self._day_sod = sod_sec()
        self._day_open_equity = self.broker.equity
        self._day_high_equity = self.broker.equity
        self._day_lock_peak_equity = self.broker.equity
        self._log(f"Engine ready. Seeded m1={len(self.m1)} h1={len(self.h1)} (src: {source})", set_status=True)
        asyncio.create_task(self._run())

    def _push_m1(self, price: float, iso: str) -> None:
        ts = float(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) if "T" in iso else float(iso)
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
        while True:
            # poll tick
            try:
                t0 = time.time()
                px, bid, ask = await poll_tick(self.client)
                t1 = time.time()
                self._last_tick_ts = int(t1)
                # naive tick latency
                self._tick_latency_ms_p95 = max(0.0, (t1 - t0) * 1000.0)

                # latency guard: three hits over threshold → pause 30m
                if (self._tick_latency_ms_p95 or 0) > settings.spec.TICK_LATENCY_HALT_MS:
                    self._latency_hits_tick.append(int(t1))
                    wins = [t for t in self._latency_hits_tick if int(t1) - t <= 300]
                    if len(wins) >= 3:
                        self._pause_until = int(time.time()) + 30 * 60
                        self._log("Latency halt: tick p95 over limit thrice, pausing 30m")

                if (bid is not None) and (ask is not None):
                    self.bid, self.ask = bid, ask
                    self._last_spread_bps = ((ask - bid) / max(1e-9, (bid + ask) / 2.0)) * 10000.0
                    # spread stability buffers
                    self._spread_bps_window.append(self._last_spread_bps or 0.0)
                    last10 = list(self._spread_bps_window)[-10:]
                    last60 = list(self._spread_bps_window)[-60:]
                    self._spread_std_10s = (pstdev(last10) if len(last10) >= 2 else 0.0)
                    self._spread_median_60s = (median(last60) if last60 else 0.0)

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

            now = int(time.time())

            # top-3 notional drop over 3s (synthetic until real OB wired)
            self._top3_hist.append((now, self._synthetic_top3_notional))
            while self._top3_hist and now - self._top3_hist[0][0] > 3:
                self._top3_hist.popleft()
            if len(self._top3_hist) >= 2:
                start_v = self._top3_hist[0][1]
                end_v = self._top3_hist[-1][1]
                self._top3_notional_drop_3s = 0.0 if start_v <= 0 else max(0.0, (start_v - end_v) / start_v)

            # append equity mark & compute 7d drawdown halt
            self._equity_marks.append((now, self.broker.equity))
            seven_days_ago = now - 7 * 86400
            marks = [e for t, e in self._equity_marks if t >= seven_days_ago]
            if marks:
                hwm = max(marks)
                dd = (self.broker.equity - hwm) / max(1e-9, hwm)
                if dd <= -0.10:
                    self._dd7_halt = True
                elif self.broker.equity >= hwm * 0.90:
                    self._dd7_halt = False

            # heartbeat stall -> flatten & pause
            if (now - self._last_tick_ts) > settings.spec.HEARTBEAT_MAX_STALL_SEC:
                if self.broker.pos:
                    self.broker.close(self.price or 0.0, exit_type="manual")
                self._pause_until = now + settings.spec.HEARTBEAT_PAUSE_MIN * 60
                self._log("Heartbeat stall: flatten & pause", set_status=False)

            # UTC day rollover
            new_sod = sod_sec()
            if new_sod != self._day_sod:
                self._day_sod = new_sod
                self._day_open_equity = self.broker.equity
                self._day_high_equity = self.broker.equity
                self._day_lock_peak_equity = self.broker.equity
                self._day_lock_armed = False
                self._day_lock_floor_pct = None
                self._losses_today = 0
                self._giveback_triggered_today = False
                self._taker_fail_events.clear()
                self._fallback_activations_today = 0
                self._fallback_session_open = False
                self._fallback_prev_nonneg = False
                self._reentry_until_ts = 0
                self._log("New UTC day: counters reset", set_status=False)

            # VS/PS
            self._update_VS_PS(now)

            # Macro pause auto-off
            if self._macro_until and now >= self._macro_until:
                self._macro_until = 0
                self.settings["macro_pause"] = False

            # Macro pause via ATR spike
            atr_ratio = self._atr_ratio_vs_median50()
            if atr_ratio is not None and atr_ratio > settings.spec.MACRO_SPIKE_MULT and not self.settings.get("macro_pause"):
                self.settings["macro_pause"] = True
                self._macro_until = now + settings.spec.MACRO_PAUSE_MIN * 60
                self._log("Macro pause: volatility spike", set_status=False)

            # Spread instability block (m1 waits 3m)
            spread_instability_block = False
            if (self._spread_std_10s is not None) and (self._spread_median_60s is not None) and self._spread_median_60s > 0:
                if (self._spread_std_10s / self._spread_median_60s) > settings.spec.SPREAD_STD_TO_MEDIAN_MAX:
                    spread_instability_block = True

            # Giveback guard
            if self.broker.equity > self._day_high_equity:
                self._day_high_equity = self.broker.equity
            runup = self._day_high_equity - self._day_open_equity
            giveback = self._day_high_equity - self.broker.equity
            gb_limit = settings.spec.GIVEBACK_PCT_OF_RUNUP / 100.0
            if self.VS >= settings.spec.GIVEBACK_TIGHT_IF["VS_GE"] and self.PS <= settings.spec.GIVEBACK_TIGHT_IF["PS_LE"]:
                gb_limit = (settings.spec.GIVEBACK_TIGHT_IF["TIGHT_TO"] / 100.0)
            if runup > 0 and giveback >= gb_limit * runup and self._pause_until < now + 1800:
                self._pause_until = now + 1800  # 30m
                self._giveback_triggered_today = True
                self._log("Giveback guard: pausing 30m", set_status=False)

            # Day‑lock controller
            if settings.spec.DAY_LOCK_ENABLE:
                peak_pct = _pct((self._day_high_equity - self._day_open_equity) / max(1e-9, self._day_open_equity))
                cur_pct = _pct((self.broker.equity - self._day_open_equity) / max(1e-9, self._day_open_equity))
                if not self._day_lock_armed and peak_pct >= settings.spec.DAY_LOCK_TRIGGER_PCT:
                    self._day_lock_armed = True
                    floor1 = settings.spec.DAY_LOCK_FLOOR_MIN_PCT
                    floor2 = (100.0 - settings.spec.DAY_LOCK_GIVEBACK_PCT) * 0.01 * peak_pct
                    self._day_lock_floor_pct = max(floor1, floor2)
                if self._day_lock_armed:
                    if cur_pct < (self._day_lock_floor_pct or 0.0):
                        self._pause_until = self._day_sod + 86400  # pause to EOD
                        self._log("Day‑lock breach: pause to EOD", set_status=False)

            # Manage open position
            if self.broker.pos and self.price is not None:
                self._manage_position(now)

            # Decide on bar closes (m1 first when VS≥1)
            m1_closed = (len(self.m1) >= 2 and self.m1[-2]["time"] != 0 and self.m1[-2]["time"] != getattr(self, "_last_m1_bar", 0))
            h1_closed = (len(self.h1) >= 2 and self.h1[-2]["time"] != 0 and self.h1[-2]["time"] != getattr(self, "_last_h1_bar", 0))
            if m1_closed or h1_closed:
                if m1_closed: setattr(self, "_last_m1_bar", self.m1[-2]["time"])
                if h1_closed: setattr(self, "_last_h1_bar", self.h1[-2]["time"])
                await self._maybe_decide(now, spread_instability_block)

            # Short status
            self._status_tick(now, spread_instability_block)

            await asyncio.sleep(0.5)

    # -------- decision/sizing/exec --------

    def _maybe_set_fallback(self, now: int) -> None:
        # honor activation cap
        if self._fallback_activations_today >= settings.spec.FALLBACK_MAX_ACTIVATIONS_PER_UTC:
            return
        # second activation only if prior window >= 0 PnL
        if self._fallback_activations_today >= 1 and not self._fallback_prev_nonneg:
            return
        # time since last h1 signal
        if (now - (self._last_h1_signal_ts or 0)) < settings.spec.FALLBACK_AFTER_HOURS_NO_H1 * 3600:
            return
        iH = len(self.h1) - 2 if len(self.h1) >= 2 else None
        if iH is None or iH < 1:
            return
        ax = adx(self.h1, 14)
        a = (ax[iH] or 0.0)
        lo, hi = settings.spec.FALLBACK_ADX_RANGE
        if lo <= a <= hi:
            self._fallback_pending_m1 = True

    async def _maybe_decide(self, now: int, spread_instability_block: bool) -> None:
        if not self.settings.get("auto_trade"):
            self.status_text = self._short_status("off"); return
        if not self._warm_ok():
            self.status_text = self._short_status("waiting"); return
        if self.settings.get("macro_pause"):
            self.status_text = self._short_status("macro"); return
        if now < self._pause_until or self._dd7_halt:
            self.status_text = self._short_status("cool"); return

        # Hard day guard (spec: halt to next UTC day)
        if self._day_pnl_pct() <= -4.0 or self._losses_today >= 4:
            self._pause_until = self._day_sod + 86400
            self._log("Hard day stop: halting to next UTC day", set_status=False)
            self.status_text = self._short_status("giveback")
            return

        # Reject if tick stale (>2s) or missing BBO (guarded quotes)
        if (now - self._last_tick_ts) > 2:
            return
        if (self.bid is None) or (self.ask is None):
            self._log("Reject: missing BBO", set_status=False)
            return

        # Red-day throttles
        red_level = 0
        day_pct = self._day_pnl_pct()
        if day_pct <= settings.spec.RED_DAY_L2_PCT:
            if settings.spec.RED_DAY_L2_HALT_NEW:
                self._log("Red‑day L2 halt", set_status=False)
                return
            red_level = 2
        elif day_pct <= settings.spec.RED_DAY_L1_PCT:
            red_level = 1

        # Arm fallback if needed (guarded)
        self._maybe_set_fallback(now)

        # Cooldowns
        cooldown_ok_m1 = (now - self._last_open_m1) > settings.spec.COOLDOWN_M1_SEC
        cooldown_ok_h1 = (now - self._last_open_h1) > 1800

        # Router context
        iC_m1 = len(self.m1) - 2 if len(self.m1) >= 2 else None
        iC_h1 = len(self.h1) - 2 if len(self.h1) >= 2 else None
        ctx = dict(
            m1=self.m1, h1=self.h1, iC_m1=iC_m1, iC_h1=iC_h1, vwap=self.vwap,
            bid=self.bid, ask=self.ask, min_bars=5, min_h1_bars=220,
            VS=self.VS, PS=self.PS, loss_streak=self._loss_streak, red_level=red_level,
        )
        order = ("m1", "h1") if self.VS >= 1.0 else ("h1", "m1")

        sig: Optional[Signal] = None
        for first in order:
            ctx["preferTF"] = first
            s = self.router.evaluate(ctx)
            # Track H1 signal timestamp
            if s.tf == "h1" and s.type in ("BUY", "SELL"):
                self._last_h1_signal_ts = now
            # Per-TF cooldowns
            if s.tf == "m1" and not cooldown_ok_m1:
                s = Signal(type="WAIT", reason="Cooldown m1")
            if s.tf == "h1" and not cooldown_ok_h1:
                s = Signal(type="WAIT", reason="Cooldown h1")
            sig = s
            if s.type != "WAIT":
                break

        # spread instability blocks only m1
        if sig and sig.tf == "m1" and spread_instability_block:
            self._log("Spread instability: m1 wait 3m", set_status=False)
            self._pause_until = max(self._pause_until, now + 180)
            return

        if sig is None or sig.type == "WAIT":
            return

        # bottom-hour m1 block (simple “bottom-2” hours: 5–6 UTC placeholder)
        blocked_bottom_hour = 0
        if sig.tf == "m1" and settings.spec.M1_BLOCK_BOTTOM_HOURS:
            hr = datetime.utcnow().hour
            if hr in (5, 6):  # can replace with buckets later
                blocked_bottom_hour = 1
                return

        # Red-day L1: “top hours only” gate for m1 entries
        if sig.tf == "m1" and red_level == 1 and settings.spec.RED_DAY_L1_TOP_HOURS_ONLY:
            if datetime.utcnow().hour not in (13, 14):
                return

        # Sizing & fee-aware targets
        if self.price is None or sig.stop_dist is None:
            return

        # Build fee constants
        maker_fee = settings.fee_maker_bps_per_side / 10000.0
        taker_fee = settings.fee_taker_bps_per_side / 10000.0
        round_trip_fee_pct_base = (taker_fee + maker_fee) if settings.assume_taker_exit_on_stops else (2 * maker_fee)

        entry_price = float(self.price)
        # ATR% (m1)
        atr_pct = self._atr_pct_m1() or 0.0

        # Effective risk % (base → VS/PS/TOD; throttle on red days)
        base_risk = (settings.spec.BASE_RISK_PCT_M1 / 100.0) if sig.tf == "m1" else (settings.spec.BASE_RISK_PCT_H1 / 100.0)

        # TOD tilt (placeholder: simple cosine around UTC noon)
        tod_mult = 1.0
        hh = datetime.utcnow().hour + datetime.utcnow().minute / 60.0
        tod_mult += (settings.spec.TOD_RISK_TILT / 100.0) * math.cos((hh - 12.0) / 24.0 * 2 * math.pi)

        eff_risk = base_risk * _clamp(self.PS, 0.25, 1.0) * tod_mult
        if red_level == 1 and sig.tf == "m1":
            eff_risk *= settings.spec.RED_DAY_L1_RISK_MULT  # 0.35×

        # fallback loosening (m1) – VS_eff shifts only band/TP math
        VS_eff = self.VS
        if sig.tf == "m1" and self._fallback_pending_m1:
            if self._fallback_activations_today < settings.spec.FALLBACK_MAX_ACTIVATIONS_PER_UTC:
                VS_eff = max(0.9, min(2.0, VS_eff + settings.spec.FALLBACK_VS_DELTA))

        # Re-entry window (≤ 11 bars): half-risk, needs micro‑triad + (top‑hour or z‑VWAP confirm)
        reentry_active = (sig.tf == "m1") and (now <= self._reentry_until_ts)
        if reentry_active:
            eff_risk *= 0.5

        # --- m1 fee-aware asym TP + A+ widen ---
        fast_tape_taker = 0
        crossing_entry = False
        post_only = True
        tp_price = None
        stop_price = None
        R = None
        tp_pct_dec_final = None
        a_plus_gate_on = 0
        asym_on = 0

        if sig.tf == "m1":
            # Base band math (VS‑adjusted)
            band_pct = sig.meta.get("band_pct") if sig.meta else None
            if band_pct is None:
                band_pct = max(settings.spec.BAND_PCT_MIN, settings.spec.BAND_PCT_ATR_MULT * atr_pct)
            tp_pct_raw = sig.meta.get("tp_pct_raw") if sig.meta else None
            if tp_pct_raw is None:
                tp_pct_raw = max(settings.spec.TP_PCT_FLOOR, settings.spec.TP_PCT_FROM_BAND_MULT * band_pct)
                tp_pct_raw *= (1 + 0.2 * max(0.0, VS_eff - 1.0))
            tp_pct_raw_dec = tp_pct_raw

            # Fee floor
            tp_floor_dec = max(settings.spec.TP_PCT_FLOOR, round_trip_fee_pct_base / settings.spec.FEE_TP_MAX_RATIO)
            stop_pct_dec = max(tp_pct_raw_dec, tp_floor_dec)

            # prelim R (unrounded) for A+ gating only
            R_prelim = abs(entry_price - (entry_price * (1 - stop_pct_dec if sig.type == "BUY" else 1 + stop_pct_dec)))

            # A+ gate
            spread_abs = (self.ask - self.bid)
            micro_triad_ok = bool((sig.meta or {}).get("micro_triad_ok", True))
            regime = (self.router.last_regime or "Range").lower()
            top2_hour = datetime.utcnow().hour in (13, 14)
            spread_to_R_pre = spread_abs / (2.0 * max(1e-9, R_prelim))
            a_plus_gate_on = int(
                settings.spec.A_PLUS_TP_ENABLE
                and top2_hour
                and micro_triad_ok
                and (regime in ("trend", "breakout"))
                and (spread_to_R_pre <= settings.spec.A_PLUS_GATE_REQ["spread_to_stop_max"])
            )
            mult = (settings.spec.A_PLUS_TP_WIDEN_MULT if a_plus_gate_on else settings.spec.ASYM_TP_WIDEN_MULT_BASE)
            tp_pct_dec = max(tp_floor_dec, tp_pct_raw_dec * (1.0 + mult * max(0.0, VS_eff - 1.0)))
            asym_on = 1

            # Tick-quantize & recompute TP%, R
            ttp = entry_price * (1 + tp_pct_dec if sig.type == "BUY" else 1 - tp_pct_dec)
            tsp = entry_price * (1 - stop_pct_dec if sig.type == "BUY" else 1 + stop_pct_dec)
            tp_price = _round_to_tick(ttp, settings.price_tick)
            stop_price = _round_to_tick(tsp, settings.price_tick)
            take_dist = abs(tp_price - entry_price)
            stop_dist = abs(entry_price - stop_price)
            tp_pct_dec = take_dist / entry_price
            R = stop_dist

        else:
            # h1 fee viability
            take_dist = sig.take_dist
            stop_dist = sig.stop_dist
            tp_pct_dec = take_dist / max(1e-9, entry_price)
            rtf_h1 = (taker_fee + maker_fee) if settings.assume_taker_exit_on_stops else (2 * maker_fee)
            if (rtf_h1 / max(1e-12, tp_pct_dec)) > settings.spec.FEE_TP_MAX_RATIO:
                self._fee_violation_events.append(now)
                self._log("Reject h1: fee_to_tp bound", set_status=False)
                return
            R = stop_dist
            tp_price = _round_to_tick(entry_price + (take_dist if sig.type == "BUY" else -take_dist), settings.price_tick)
            stop_price = _round_to_tick(entry_price - (stop_dist if sig.type == "BUY" else -stop_dist), settings.price_tick)
            post_only, fast_tape_taker, crossing_entry = True, 0, False
            tp_pct_dec_final = abs(tp_price - entry_price) / max(1e-9, entry_price)

        # Quantity sizing & leverage caps
        equity = max(1e-9, float(self.broker.equity))
        qty = (equity * eff_risk) / max(1e-9, R)

        # Leverage caps: 10× if spread tight, else 5× (h1=2×)
        spread_abs_for_caps = (self.ask - self.bid)
        slip_est_post = spread_abs_for_caps / 2.0
        lev_cap = 2.0 if sig.tf == "h1" else (10.0 if (self._last_spread_bps or 999) <= 4.0 else 5.0)
        qty_cap = (equity * lev_cap) / max(1.0, entry_price)
        qty = max(0.0, min(qty, qty_cap))
        qty = _round_to_tick(qty, settings.qty_tick)

        # Combined live risk cap (single pos paper)
        if self.broker.pos:
            live = (self.broker.pos.stop_dist * self.broker.pos.qty) / equity
            if (live + eff_risk) > (settings.spec.LIVE_RISK_CAP / 100.0):
                return
        else:
            if eff_risk > (settings.spec.LIVE_RISK_CAP / 100.0):
                return

        order_notional = qty * entry_price
        top3_notional = self._synthetic_top3_notional

        # ---- Fast‑tape taker admission & 1× bump ----
        round_trip_fee_pct = round_trip_fee_pct_base
        fast_tape_disabled = int(now < self._fast_tape_disabled_until)

        macd_l, macd_s = self._macd_m1
        idx = len(self.m1) - 2
        macd_hist_now = ((macd_l[idx] or 0.0) - (macd_s[idx] or 0.0)) if (sig.tf == "m1" and macd_l and macd_s and idx is not None and idx < len(macd_l)) else 0.0
        macd_hist_prev = ((macd_l[idx - 1] or 0.0) - (macd_s[idx - 1] or 0.0)) if (sig.tf == "m1" and macd_l and macd_s and idx and idx-1 < len(macd_l)) else 0.0
        macd_accel_ok = (macd_hist_prev != 0 and macd_hist_now >= settings.spec.RUNNER_ACCEL_MACD_MULT * macd_hist_prev)

        consider_taker = False
        spread_to_R = None

        if sig.tf == "m1":
            spread_abs = (self.ask - self.bid)
            spread_to_R = spread_abs / (2.0 * max(1e-9, R))
            micro_triad_ok = bool((sig.meta or {}).get("micro_triad_ok", True))
            consider_taker = (not fast_tape_disabled) \
                             and (macd_accel_ok if settings.spec.FAST_TAPE_NEED_MACD_ACCEL else True) \
                             and micro_triad_ok \
                             and (top3_notional >= 3.0 * order_notional) \
                             and (spread_to_R <= 0.05)

            # top‑3 crumble guard: if drop > 50% in 3s → reject taker attempt & fallback to maker (no fail)
            if self._top3_notional_drop_3s > settings.spec.TOP3_CRUMBLE_MAX_DROP_PCT:
                consider_taker = False

            if consider_taker:
                # One-time TP bump by paid spread
                tp_price_pre_bump = tp_price
                bump = spread_abs if sig.type == "BUY" else -spread_abs
                tp_price = _round_to_tick(tp_price + bump, settings.price_tick)
                take_dist = abs(tp_price - entry_price)
                tp_pct_dec = take_dist / entry_price
                round_trip_fee_pct = taker_fee + maker_fee
                fast_tape_taker, crossing_entry, post_only = 1, True, False
                if (round_trip_fee_pct / max(1e-12, tp_pct_dec)) > settings.spec.FAST_TAPE_TAKER_MAX_FEE_TO_TP:
                    # fallback to maker; counts as taker fail
                    fast_tape_taker, crossing_entry, post_only = 0, False, True
                    tp_price = tp_price_pre_bump
                    take_dist = abs(tp_price - entry_price)
                    tp_pct_dec = take_dist / entry_price
                    round_trip_fee_pct = round_trip_fee_pct_base
                    self._taker_fail_events.append(now)

        # Depth / slip & shrink‑to‑fit
        impact_component = settings.slip_coeff_k * (order_notional / max(1e-9, top3_notional)) * R if top3_notional > 0 else None
        slip_est_maker = slip_est_post
        slip_est_taker = (slip_est_post + (impact_component or 0.0)) if fast_tape_taker else slip_est_post
        slip_R = ((slip_est_taker if fast_tape_taker else slip_est_maker) / max(1e-9, R))
        iters = 0
        while iters < settings.max_shrink_iters:
            if top3_notional < settings.top3x_order_notional_min * order_notional:
                qty *= 0.92
            elif slip_R > 0.30:
                qty *= 0.92
            else:
                break
            qty = _round_to_tick(qty, settings.qty_tick)
            order_notional = qty * entry_price
            impact_component = settings.slip_coeff_k * (order_notional / max(1e-9, top3_notional)) * R if top3_notional > 0 else None
            slip_est_taker = (slip_est_post + (impact_component or 0.0)) if fast_tape_taker else slip_est_post
            slip_R = ((slip_est_taker if fast_tape_taker else slip_est_maker) / max(1e-9, R))
            iters += 1
        if iters >= settings.max_shrink_iters and (top3_notional < settings.top3x_order_notional_min * order_notional or slip_R > 0.30):
            return

        # Exchange min notional guards
        if settings.exchange_min_notional > 0:
            if order_notional < 1.05 * settings.exchange_min_notional:
                return
        if settings.min_notional_usd > 0 and order_notional < settings.min_notional_usd:
            return

        # Final fee viability & QA assertion
        if sig.tf == "m1":
            fee_to_tp = ( (taker_fee + maker_fee) if fast_tape_taker else round_trip_fee_pct_base ) / max(1e-12, tp_pct_dec)
            fee_bound = (settings.spec.FAST_TAPE_TAKER_MAX_FEE_TO_TP if fast_tape_taker else settings.spec.FEE_TP_MAX_RATIO)
            if (fast_tape_taker == 0 and abs(fee_bound - settings.spec.FEE_TP_MAX_RATIO) > 1e-9) or (fast_tape_taker == 1 and abs(fee_bound - settings.spec.FAST_TAPE_TAKER_MAX_FEE_TO_TP) > 1e-9):
                self._log("QA fee_bound mismatch; correcting")
                fee_bound = (settings.spec.FAST_TAPE_TAKER_MAX_FEE_TO_TP if fast_tape_taker else settings.spec.FEE_TP_MAX_RATIO)
            if fee_to_tp > fee_bound:
                self._fee_violation_events.append(now)
                last10m = [t for t in self._fee_violation_events if now - t <= 600]
                if len(last10m) >= settings.spec.FEE_TP_VIOLATIONS_IN_10M:
                    self._pause_until = now + settings.spec.PAUSE_AFTER_FEE_TP_BREAK_MIN * 60
                self._log("Reject m1: fee_to_tp bound", set_status=False)
                return
            tp_pct_dec_final = tp_pct_dec

        # Re-entry guard (must also have micro‑triad + (top‑hour or z‑VWAP confirm))
        if reentry_active:
            ok_top_hour = (datetime.utcnow().hour in (13, 14))
            zv = (sig.meta or {}).get("z_vwap")
            ok_z = (zv is not None)
            micro_ok = bool((sig.meta or {}).get("micro_triad_ok", False))
            if not (micro_ok and (ok_top_hour or ok_z)):
                return

        # Record telemetry meta
        self._last_slip_est = (slip_est_taker if fast_tape_taker else slip_est_maker)
        self._last_fee_to_tp = ( (taker_fee + maker_fee) if fast_tape_taker else ( (taker_fee + maker_fee) if settings.assume_taker_exit_on_stops else (2*maker_fee) ) ) / max(1e-12, tp_pct_dec_final or 0.0)
        meta = {
            "strategy": self.router.last_strategy,
            "regime": self.router.last_regime,
            "VS": self.VS, "PS": self.PS,
            "loss_streak": self._loss_streak,
            "spread_bps": self._last_spread_bps,
            "spread_std_10s": self._spread_std_10s,
            "spread_median_60s": self._spread_median_60s,
            "top3_notional": top3_notional,
            "order_notional": order_notional,
            "impact_component": impact_component,
            "slip_est": self._last_slip_est,
            "spread_to_stop_ratio": ( ((self.ask - self.bid)) / max(1e-9, (2.0 * R)) ),
            "assumed_fee_model": "TM" if settings.assume_taker_exit_on_stops else "MM",
            "round_trip_fee_pct": (taker_fee + maker_fee) if fast_tape_taker else ((taker_fee + maker_fee) if settings.assume_taker_exit_on_stops else (2*maker_fee)),
            "fee_to_tp": self._last_fee_to_tp,
            "tp_fee_floor": max(settings.spec.TP_PCT_FLOOR, ( (settings.fee_taker_bps_per_side/10000.0 + settings.fee_maker_bps_per_side/10000.0) if settings.assume_taker_exit_on_stops else (2*settings.fee_maker_bps_per_side/10000.0) ) / settings.spec.FEE_TP_MAX_RATIO),
            "final_stop_dist_R": R,
            "final_tp_pct": tp_pct_dec_final,
            "tp_price": tp_price,
            "post_only": post_only,
            "fast_tape_taker": fast_tape_taker,
            "crossing_entry": crossing_entry,
            "a_plus_gate_on": a_plus_gate_on,
            "asym_m1_on": asym_on,
            "day_lock_armed": int(self._day_lock_armed),
            "day_lock_floor_pct": self._day_lock_floor_pct,
            "red_day_throttle_level": red_level,
            "fast_tape_disabled": int(now < self._fast_tape_disabled_until),
            "taker_fail_count_30m": len([t for t in self._taker_fail_events if now - t <= settings.spec.FAST_TAPE_DISABLE_WINDOW_MIN * 60]),
            "tick_p95_ms": self._tick_latency_ms_p95,
            "order_ack_p95_ms": self._order_ack_p95_ms,
            "spread_instability_block": int(spread_instability_block),
            "top3_notional_drop_pct_3s": self._top3_notional_drop_3s,
            "cooldown_bonus_on": 0,
            "score": sig.score,
            "z_vwap": (sig.meta or {}).get("z_vwap"),
            "blocked_bottom_hour": blocked_bottom_hour,
        }

        # Telemetry requirement sanity
        critical_fields = ["final_stop_dist_R", "final_tp_pct", "tp_price", "tp_fee_floor", "fee_to_tp", "round_trip_fee_pct"]
        if any(meta.get(k) is None for k in critical_fields):
            self._log("Reject: missing required telemetry", set_status=False)
            return

        # Close & reverse if opposite
        if self.broker.pos:
            ps = self.broker.pos.side
            if (sig.type == "BUY" and ps == "short") or (sig.type == "SELL" and ps == "long"):
                self.broker.close(entry_price, exit_type="reverse")
                self._log("Reversed position", set_status=False)

        # Open
        if not self.broker.pos and qty > 0.0:
            # Cooldown relax in top hours (if clean tape + not already lost)
            if sig.tf == "m1":
                hr = datetime.utcnow().hour
                in_top = hr in (13, 14)
                spread_to_R_for_cd = meta["spread_to_stop_ratio"]
                slip_R_for_cd = (self._last_slip_est or 0)/max(1e-9, R)
                if in_top and (spread_to_R_for_cd <= settings.spec.COOLDOWN_TOP_HOUR_GATE["spread_to_stop_max"]) \
                   and (slip_R_for_cd <= settings.spec.COOLDOWN_TOP_HOUR_GATE["slip_R_max"]) \
                   and (0.9 <= self.VS <= 1.4) and (self.PS >= 0.60) \
                   and (not self._lost_in_hour.get(hr, False)):
                    self._last_open_m1 = now - settings.spec.COOLDOWN_M1_SEC + settings.spec.COOLDOWN_M1_SEC_TOP_HOUR
                    meta["cooldown_bonus_on"] = 1

            side = sig.type
            stop = stop_price
            take = tp_price
            self.broker.open(
                side=side, entry=entry_price, qty=qty, stop=stop, take=take, stop_dist=R,
                maker_fee_rate=(settings.fee_maker_bps_per_side/10000.0),
                taker_fee_rate=(settings.fee_taker_bps_per_side/10000.0),
                post_only=post_only, fast_tape_taker=fast_tape_taker, crossing_entry=crossing_entry,
                tf=sig.tf or "m1", scratch_after_sec=240, opened_by=self.router.last_strategy, meta=meta,
            )
            if sig.tf == "m1":
                self._last_open_m1 = now
                # taker fail self-throttle
                fails_30m = [t for t in self._taker_fail_events if now - t <= settings.spec.FAST_TAPE_DISABLE_WINDOW_MIN * 60]
                if len(fails_30m) >= settings.spec.FAST_TAPE_DISABLE_AFTER_FAILS:
                    self._fast_tape_disabled_until = now + settings.spec.FAST_TAPE_DISABLE_COOLDOWN_MIN * 60
                if self._fallback_pending_m1:
                    # start fallback window tracking
                    if self._fallback_activations_today == 0:
                        self._fallback_session_open = True
                        self._fallback_session_start_equity = self.broker.equity
                    self._fallback_pending_m1 = False
                    self._fallback_activations_today += 1
            else:
                self._last_open_h1 = now

            self._log(
                f"Open {sig.type} {sig.tf} @ {entry_price:.2f} qty={qty:.6f} R=${R:.2f} score={sig.score} "
                f"(spread={self._last_spread_bps or 0:.2f}bps slip_est=${self._last_slip_est or 0:.2f} top3=${self._synthetic_top3_notional:,.0f})",
                set_status=False
            )

    # -------- management --------

    def _manage_position(self, now: int) -> None:
        _ = self.broker.mark(self.price or 0.0)
        p = self.broker.pos
        if not p:
            return
        R = p.stop_dist

        # Partial per spec
        hit_partial = (self.price >= p.entry + settings.spec.PARTIAL_AT_R * R) if p.side == "long" else (self.price <= p.entry - settings.spec.PARTIAL_AT_R * R)
        partial_frac = None
        partial_at_R = settings.spec.PARTIAL_AT_R
        if p.tf == "m1" and self.VS >= settings.spec.PARTIAL_M1_HOTVS_SHIFT["VS_GE"]:
            partial_at_R = settings.spec.PARTIAL_M1_HOTVS_SHIFT["PARTIAL_AT_R"]
            hit_partial = (self.price >= p.entry + partial_at_R * R) if p.side == "long" else (self.price <= p.entry - partial_at_R * R)
            partial_frac = settings.spec.PARTIAL_M1_HOTVS_SHIFT["PARTIAL_FRACTION"]
        if hit_partial and not p.partial_taken:
            if partial_frac is None:
                partial_frac = 0.25 if ("breakout" in (p.opened_by or "").lower() or "trend" in (p.opened_by or "").lower()) else 0.30
                if p.tf == "m1": partial_frac = 0.60
            self.broker.partial_close(partial_frac, self.price, exit_type="partial")
            if self.broker.pos:
                be_off = settings.spec.BE_BUFFER_R * R
                self.broker.pos.stop = self.broker.pos.entry + (be_off if p.side == "long" else -be_off)
                self.broker.pos.be = True
                self.broker.pos.partial_taken = True
            self.status_text = self._short_status("partial")

        # Add‑1 pyramid
        if self.broker.pos and p.tf == "m1" and p.partial_taken and p.be and not (p.meta or {}).get("pyramid_adds"):
            equity = max(1e-9, float(self.broker.equity))
            add_risk = min( (settings.spec.LIVE_RISK_CAP/100.0) - (p.stop_dist * p.qty)/equity, (settings.spec.BASE_RISK_PCT_M1/100.0) * 0.5 )
            if add_risk > 0:
                add_qty = (equity * add_risk) / max(1e-9, p.stop_dist)
                if add_qty > 0:
                    self.broker.scale_in(add_qty, self.price or p.entry)
                    if self.broker.pos and self.broker.pos.meta is not None:
                        self.broker.pos.meta["pyramid_adds"] = "Add-1"

        # Add‑2 pyramid (profit‑funded)
        if self.broker.pos and p.tf == "m1" and (p.meta or {}).get("pyramid_adds") == "Add-1":
            equity = max(1e-9, float(self.broker.equity))
            unreal = self.broker.mark(self.price or p.entry)
            avail_risk_cap = (settings.spec.LIVE_RISK_CAP/100.0) - (p.stop_dist * p.qty)/equity
            add_risk = min(avail_risk_cap, (settings.spec.BASE_RISK_PCT_M1/100.0) * 0.5)
            needed_usd = add_risk * equity
            if unreal > needed_usd and add_risk > 0:
                add_qty = (equity * add_risk) / max(1e-9, p.stop_dist)
                if add_qty > 0:
                    self.broker.scale_in(add_qty, self.price or p.entry)
                    if self.broker.pos and self.broker.pos.meta is not None:
                        self.broker.pos.meta["pyramid_adds"] = "Add-1+2"

        # RSI extreme extra scale-out 25%
        if self.broker.pos and not self.broker.pos.extra_scaled:
            idx = (len(self.m1) - 2) if p.tf == "m1" else (len(self.h1) - 2)
            rsi_now = (self._rsi_m1[idx] if p.tf == "m1" else self._rsi_h1[idx]) if idx is not None else None
            if rsi_now is not None:
                if (p.side == "long" and rsi_now > 80.0) or (p.side == "short" and rsi_now < 20.0):
                    self.broker.partial_close(0.25, self.price, exit_type="scale")
                    if self.broker.pos:
                        self.broker.pos.extra_scaled = True

        # Trail
        if self.broker.pos:
            tighten = False
            if p.tf == "m1":
                line, sig = self._macd_m1; i = len(self.m1) - 2
            else:
                line, sig = self._macd_h1; i = len(self.h1) - 2
            if i is not None and i > 1 and i < len(line):
                cur = (line[i] or 0.0) - (sig[i] or 0.0)
                prev = (line[i - 1] or 0.0) - (sig[i - 1] or 0.0)
                if (p.side == "long" and cur < prev) or (p.side == "short" and cur > prev):
                    tighten = True
            k = (settings.spec.TRAIL_R_TIGHT_ON_MACD_FADE if tighten else settings.spec.TRAIL_R_VS) * self.VS
            if p.side == "long":
                new_stop = self.price - (k * R)
                if new_stop > p.stop: p.stop = new_stop
            else:
                new_stop = self.price + (k * R)
                if new_stop < p.stop: p.stop = new_stop
            self.status_text = self._short_status("trailing")

        # Runner accel ratchet
        if self.broker.pos:
            line, sig = (self._macd_m1 if p.tf == "m1" else self._macd_h1)
            i = (len(self.m1) - 2) if p.tf == "m1" else (len(self.h1) - 2)
            macd_hist_now = ((line[i] or 0.0) - (sig[i] or 0.0)) if (line and sig and i is not None and i < len(line)) else 0.0
            macd_hist_prev = ((line[i - 1] or 0.0) - (sig[i - 1] or 0.0)) if (line and sig and i and i-1 < len(line)) else 0.0
            ratchet_at = settings.spec.RUNNER_RATCHET_AT_R
            if settings.spec.RUNNER_ACCEL_ENABLE and macd_hist_prev != 0 and macd_hist_now >= settings.spec.RUNNER_ACCEL_MACD_MULT * macd_hist_prev:
                ratchet_at = min(ratchet_at, settings.spec.RUNNER_RATCHET_AT_R_ACCEL)
                if self.broker.pos.meta:
                    self.broker.pos.meta["runner_ratchet_early"] = 1
            max_open_R = ( (self.price - p.entry) if p.side=="long" else (p.entry - self.price) ) / max(1e-9, R)
            if max_open_R >= ratchet_at:
                floor = 1.2 * R
                if p.side == "long": p.stop = max(p.stop, self.price - floor)
                else: p.stop = min(p.stop, self.price + floor)

        # Time‑scratch (m1, VS<1): +0.25R not in 4 min -> BE
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
            net = self.broker.close(p.take if hit_take else self.price, exit_type=("take" if hit_take else "stop"))
            r_mult = (net / max(1e-9, base_R_usd)) if net is not None else 0.0
            self._last_Rs.append(r_mult); self._last_Rs = self._last_Rs[-20:]
            self._log(f"Close {'TAKE' if hit_take else 'STOP'} {p.tf} PnL {net:+.2f} ({r_mult:+.2f}R)", set_status=False)

            # update loss streak & pauses
            if net is not None and net > 0:
                self._loss_streak = 0.0
            else:
                self._loss_streak += 1.0
                self._losses_today += 1
                hr = datetime.utcnow().hour
                self._lost_in_hour[hr] = True
                if self._loss_streak >= 4.0:
                    self._pause_until = self._day_sod + 86400  # end day
                elif self._loss_streak >= 3.0:
                    self._pause_until = int(time.time()) + 2700  # 45m
                elif self._loss_streak >= 2.0:
                    self._pause_until = int(time.time()) + 900   # 15m

            # close fallback window tracking when applicable
            if self._fallback_session_open:
                delta = self.broker.equity - self._fallback_session_start_equity
                self._fallback_prev_nonneg = (delta >= 0.0)
                self._fallback_session_open = False

            # Start re-entry window (m1 only)
            if p.tf == "m1":
                self._reentry_until_ts = int(time.time()) + settings.spec.REENTRY_MAX_BARS * settings.spec.tf_m1

    # -------- status --------

    def _status_tick(self, now: int, spread_instability_block: bool) -> None:
        if not self.settings.get("auto_trade"):
            self.status_text = self._short_status("off"); return
        if self.settings.get("macro_pause"):
            self.status_text = self._short_status("macro"); return
        if now < self._pause_until:
            self.status_text = self._short_status("cool"); return
        if self.broker.pos:
            self.status_text = self._short_status("managing"); return

        r = (self.logs[-1]["text"].lower() if self.logs else "")
        if "atr" in r or "quiet" in r:
            self.status_text = self._short_status("vol_low")
        elif "wild" in r or "macro" in r:
            self.status_text = self._short_status("vol_high")
        elif "spread" in r or spread_instability_block:
            self.status_text = self._short_status("spread")
        elif "fee" in r:
            self.status_text = self._short_status("fees")
        elif "trend" in r:
            self.status_text = self._short_status("trend")
        elif "break" in r:
            self.status_text = self._short_status("break")
        else:
            self.status_text = self._short_status("waiting")
