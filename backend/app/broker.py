"""A simple paper broker for the Ultimate Bot.

The ``PaperBroker`` simulates order fills, calculates fees, updates equity,
and maintains an in‑memory history of closed trades. It supports basic
long/short position management with fixed fee rates for maker and taker
orders. During development or paper trading, this component provides the
mechanics for opening, closing, partial closes, and marking positions
without connecting to real exchanges.
"""

from __future__ import annotations

from time import time
from .models import Position, Trade

FEE_MAKER = 0.0001
FEE_TAKER = 0.0002


class PaperBroker:
    """Simulates trade executions and manages paper account state."""

    def __init__(self, start_equity: float):
        # Account balance used for position sizing
        self.equity = start_equity
        # Current open position, if any
        self.pos: Position | None = None
        # History of completed trades (including partials)
        self.history: list[Trade] = []

    def _now(self) -> int:
        """Return the current timestamp as an integer."""
        return int(time())

    def open(
        self,
        side: str,
        entry: float,
        qty: float,
        stop: float,
        take: float,
        stop_dist: float,
        maker: bool = True,
        *,
        tf: str = "m1",
        scratch_after_sec: int = 300,
        opened_by: str | None = None,
        meta: dict | None = None,
    ) -> None:
        """Open a new position.

        Args:
            side: 'BUY' for long, 'SELL' for short.
            entry: Entry price.
            qty: Quantity to trade.
            stop: Stop price.
            take: Take profit price.
            stop_dist: Distance between entry and stop (1R).
            maker: Whether to use maker or taker fee.
            tf: 'm1' (scalper) or 'h1' (swing).
            scratch_after_sec: optional time-based scratch window (scalper).
            opened_by: strategy name that opened the trade (for H1 partials policy).
            meta: optional metadata for telemetry (strategy/regime/VS/PS/score/etc).
        """
        fee_rate = FEE_MAKER if maker else FEE_TAKER
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
            meta=meta or {},
        )

    def _close_amount(self, qty_to_close: float, px: float) -> float:
        """Close a quantity from the current position at px; return net PnL."""
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
                # Telemetry (optional, ignored by UI table but useful for logs)
                tf=p.tf,
                strategy=p.opened_by or meta.get("strategy"),
                regime=meta.get("regime"),
                vs=meta.get("VS"),
                ps=meta.get("PS"),
                spread_bps=meta.get("spread_bps"),
                slip_est=meta.get("slip_est"),
                fee_to_tp=meta.get("fee_to_tp"),
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
        """Partially close a fraction of the open position at px."""
        if not self.pos or fraction <= 0.0 or fraction >= 1.0:
            return None
        qty_to_close = self.pos.qty * fraction
        return self._close_amount(qty_to_close, px)

    def close(self, px: float) -> float | None:
        """Close the current position at ``px``."""
        if not self.pos:
            return None
        p = self.pos
        net = self._close_amount(p.qty, px)
        self.pos = None
        return net

    def mark(self, px: float) -> float:
        """Mark the open position to market and compute unrealized PnL."""
        if not self.pos:
            return 0.0
        p = self.pos
        # Update the high/low watermarks
        p.hi = max(p.hi, px)
        p.lo = min(p.lo, px)
        gross = (px - p.entry) * p.qty if p.side == "long" else (p.entry - px) * p.qty
        exit_fee = px * p.qty * p.fee_rate
        paid_fees = p.entry * p.qty * p.fee_rate
        return gross - (exit_fee + paid_fees)
