"""A simple paper broker for the Ultimate Bot.

The ``PaperBroker`` simulates order fills, calculates fees, updates equity,
and maintains an in‑memory history of closed trades. It supports basic
long/short position management with fixed fee rates for maker and taker
orders. During development or paper trading, this component provides the
mechanics for opening, closing, and marking positions without connecting to
real exchanges.
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
        # History of completed trades
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
    ) -> None:
        """Open a new position.

        Args:
            side: 'BUY' for long, 'SELL' for short.
            entry: Entry price.
            qty: Quantity to trade.
            stop: Stop price.
            take: Take profit price.
            stop_dist: Distance between entry and stop.
            maker: Whether to use maker or taker fee.
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
        )

    def close(self, px: float) -> float | None:
        """Close the current position at ``px``.

        Args:
            px: Exit price.

        Returns:
            The net profit/loss of the trade, or ``None`` if no position.
        """
        if not self.pos:
            return None
        p = self.pos
        gross = (px - p.entry) * p.qty if p.side == "long" else (p.entry - px) * p.qty
        fees = (p.entry + px) * p.qty * p.fee_rate
        net = gross - fees
        self.equity += net
        self.history.append(
            Trade(
                side=p.side,
                entry=p.entry,
                close=px,
                pnl=net,
                open_time=p.open_time,
                close_time=self._now(),
            )
        )
        self.pos = None
        return net

    def mark(self, px: float) -> float:
        """Mark the open position to market and compute unrealized PnL.

        Args:
            px: Current price.

        Returns:
            Unrealized net PnL.
        """
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
