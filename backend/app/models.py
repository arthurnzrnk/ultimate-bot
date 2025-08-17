"""Data models for the Ultimate Bot backend.

This module defines the Pydantic models that structure the data exchanged
between the backend engine and the API. It includes candle definitions,
position and trade records, user‑modifiable settings, and status snapshots
sent to the frontend. Models ensure type safety and straightforward
serialization/deserialization when communicating over HTTP.
"""

from pydantic import BaseModel
from typing import Literal, Optional, List

# Literal type for trade sides
Side = Literal["long", "short"]


class Candle(BaseModel):
    """Represents a single market data candle."""
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class Position(BaseModel):
    """Represents an open trading position."""
    side: Side
    qty: float
    entry: float
    stop: float
    take: float
    stop_dist: float
    fee_rate: float
    open_time: int
    hi: float
    lo: float
    be: bool = False  # breakeven flag


class Trade(BaseModel):
    """Represents a completed trade for the history."""
    side: Side
    entry: float
    close: float
    pnl: float
    open_time: int
    close_time: int


class Settings(BaseModel):
    """User-configurable bot settings."""
    scalp_mode: bool = True
    auto_trade: bool = True
    strategy: str = "Level King — Regime"
    macro_pause: bool = False


class Status(BaseModel):
    """Snapshot of the bot state returned to the frontend."""
    price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    status: str = "Loading..."
    equity: float
    pos: Optional[Position] = None
    history: List[Trade] = []
    candles: List[Candle] = []
    scalpMode: bool = True
    autoTrade: bool = True
    strategy: str = "Level King — Regime"
    fillsToday: int = 0
    pnlToday: float = 0.0
    unrealNet: float = 0.0