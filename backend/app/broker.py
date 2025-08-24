"""Paper broker for Strategy V3.4.

- Maker/taker fee rates read from settings at engine open().
- Tracks equity and history; returns realized R on closes.
"""

from __future__ import annotations
from time import time
from .models import Position, Trade


class PaperBroker:
    def __init__(self, start_equity: float):
        self.equity = start_equity
        self.pos: Position | None = None
        self.history: list[Trade] = []

    def _now(self) -> int:
        return int(time())

    def open(
        self,
        side: str,
        entry: float,
        qty: float,
        stop: float,
        take: float,
        stop_dist: float,
        *,
        maker_fee_rate: float,
        taker_fee_rate: float,
        post_only: bool,
        fast_tape_taker: int,
        crossing_entry: bool,
        tf: str,
        scratch_after_sec: int,
        opened_by: str | None,
        meta: dict | None,
    ) -> None:
        fee_rate = maker_fee_rate if post_only else taker_fee_rate
        self.pos = Position(
            side="long" if side.upper() == "BUY" else "short",
            qty=qty,
            entry=entry,
            stop=stop,
            take=take,
            stop_dist=stop_dist,
            fee_rate=fee_rate,
            open_time=self._now(),
            hi=entry,
            lo=entry,
            be=False,
            tf="m1" if tf == "m1" else "h1",
            partial_taken=False,
            scratch_after_sec=scratch_after_sec,
            opened_by=opened_by,
            extra_scaled=False,
            meta={
                **(meta or {}),
                "post_only": bool(post_only),
                "fast_tape_taker": int(fast_tape_taker),
                "crossing_entry": bool(crossing_entry),
                "maker_fee_rate": maker_fee_rate,
                "taker_fee_rate": taker_fee_rate,
            },
        )

    # --- new: scale-in for pyramids (keeps stop/take intact) ---
    def scale_in(self, add_qty: float, add_entry: float) -> None:
        if not self.pos or add_qty <= 0:
            return
        p = self.pos
        # volume-weighted average entry
        new_qty = p.qty + add_qty
        p.entry = (p.entry * p.qty + add_entry * add_qty) / max(1e-9, new_qty)
        p.qty = new_qty
        p.hi = max(p.hi, add_entry)
        p.lo = min(p.lo, add_entry)
        # recompute 1R in $ vs new averaged entry distance to current stop
        p.stop_dist = abs(p.entry - p.stop)

    def _close_amount(self, qty_to_close: float, px: float) -> float:
        if not self.pos or qty_to_close <= 0:
            return 0.0
        p = self.pos
        gross = (px - p.entry) * qty_to_close if p.side == "long" else (p.entry - px) * qty_to_close
        fees = (p.entry + px) * qty_to_close * p.fee_rate
        net = gross - fees
        self.equity += net
        base_R_usd = p.stop_dist * qty_to_close
        r_mult = (net / base_R_usd) if base_R_usd > 0 else None
        meta = p.meta or {}
        self.history.append(
            Trade(
                side=p.side,
                entry=p.entry,
                close=px,
                pnl=net,
                open_time=p.open_time,
                close_time=self._now(),
                r_multiple=r_mult,
                tf=p.tf,
                strategy=p.opened_by or meta.get("strategy"),
                regime=meta.get("regime"),
                vs=meta.get("VS"),
                ps=meta.get("PS"),
                loss_streak=meta.get("loss_streak"),  # keep streak snapshot
                spread_bps=meta.get("spread_bps"),
                spread_std_10s=meta.get("spread_std_10s"),
                spread_median_60s=meta.get("spread_median_60s"),
                top3_notional=meta.get("top3_notional"),
                order_notional=meta.get("order_notional"),
                impact_component=meta.get("impact_component"),
                slip_est=meta.get("slip_est"),
                spread_to_stop_ratio=meta.get("spread_to_stop_ratio"),
                z_vwap=meta.get("z_vwap"),
                assumed_fee_model=meta.get("assumed_fee_model"),
                round_trip_fee_pct=meta.get("round_trip_fee_pct"),
                fee_to_tp=meta.get("fee_to_tp"),
                tp_fee_floor=meta.get("tp_fee_floor"),
                final_stop_dist_R=meta.get("final_stop_dist_R"),
                final_tp_pct=meta.get("final_tp_pct"),
                entry_price=p.entry,
                tp_price=meta.get("tp_price"),
                stop_price=p.stop,
                post_only=meta.get("post_only"),
                fast_tape_taker=meta.get("fast_tape_taker"),
                crossing_entry=meta.get("crossing_entry"),
                partials=meta.get("partials"),
                pyramid_adds=meta.get("pyramid_adds"),
                trail_events=meta.get("trail_events"),
                win_R=meta.get("win_R"),
                loss_R=meta.get("loss_R"),
                realized_R=r_mult,
                reject_reason=meta.get("reject_reason"),
                asym_m1_on=meta.get("asym_m1_on"),
                day_lock_armed=meta.get("day_lock_armed"),
                day_lock_floor_pct=meta.get("day_lock_floor_pct"),
                red_day_throttle_level=meta.get("red_day_throttle_level"),
                blocked_bottom_hour=meta.get("blocked_bottom_hour"),
                runner_ratchet_early=meta.get("runner_ratchet_early"),
                a_plus_gate_on=meta.get("a_plus_gate_on"),
                fast_tape_disabled=meta.get("fast_tape_disabled"),
                taker_fail_count_30m=meta.get("taker_fail_count_30m"),
                latency_halt=meta.get("latency_halt"),
                tick_p95_ms=meta.get("tick_p95_ms"),
                order_ack_p95_ms=meta.get("order_ack_p95_ms"),
                spread_instability_block=meta.get("spread_instability_block"),
                top3_crumble_block=meta.get("top3_crumble_block"),
                top3_notional_drop_pct_3s=meta.get("top3_notional_drop_pct_3s"),
                cooldown_bonus_on=meta.get("cooldown_bonus_on"),
                score=meta.get("score"),
                vol_multiple=meta.get("vol_multiple"),
                candle_type=meta.get("candle_type"),
            )
        )
        p.qty = max(0.0, p.qty - qty_to_close)
        if p.qty == 0.0:
            self.pos = None
        return net

    def partial_close(self, fraction: float, px: float) -> float | None:
        if not self.pos or fraction <= 0.0 or fraction >= 1.0:
            return None
        qty_to_close = self.pos.qty * fraction
        return self._close_amount(qty_to_close, px)

    def close(self, px: float) -> float | None:
        if not self.pos:
            return None
        p = self.pos
        net = self._close_amount(p.qty, px)
        self.pos = None
        return net

    def mark(self, px: float) -> float:
        if not self.pos:
            return 0.0
        p = self.pos
        p.hi = max(p.hi, px)
        p.lo = min(p.lo, px)
        gross = (px - p.entry) * p.qty if p.side == "long" else (p.entry - px) * p.qty
        exit_fee = px * p.qty * p.fee_rate
        paid_fees = p.entry * p.qty * p.fee_rate
        return gross - (exit_fee + paid_fees)
