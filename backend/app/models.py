"""Data models for the Ultimate Bot backend."""

from pydantic import BaseModel, Field
from typing import Literal, Optional, List

Side = Literal["long", "short"]


class Candle(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class Position(BaseModel):
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
    side: Side
    entry: float
    close: float
    pnl: float
    open_time: int
    close_time: int


class Settings(BaseModel):
    scalp_mode: bool = True
    auto_trade: bool = True
    strategy: str = "Level King — Regime"
    macro_pause: bool = False


class Status(BaseModel):
    price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    status: str = "Loading..."
    equity: float
    pos: Optional[Position] = None
    history: List[Trade] = Field(default_factory=list)
    candles: List[Candle] = Field(default_factory=list)

    # UI flags
    scalpMode: bool = True
    autoTrade: bool = True

    # Strategy labels
    strategy: str = "Level King — Regime"           # overall (from settings)
    activeStrategy: Optional[str] = None            # router-selected sub-strategy

    # Telemetry for explanations
    regime: Optional[str] = None                    # Range | Trending | Breakout
    bias: Optional[str] = None                      # Bullish | Bearish
    adx: Optional[float] = None
    atrPct: Optional[float] = None                  # e.g., 0.0043 (0.43%)
    fillsToday: int = 0
    pnlToday: float = 0.0
    unrealNet: float = 0.0
