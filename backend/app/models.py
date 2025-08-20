"""Data models for the Ultimate Bot backend."""

from pydantic import BaseModel, Field
from typing import Literal, Optional, List

Side = Literal["long", "short"]
ProfileMode = Literal["LIGHT", "HEAVY", "AUTO"]


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

    # V2 additions
    tf: Literal["m1", "h1"] = "m1"            # which timeframe strategy opened this
    profile: Literal["LIGHT", "HEAVY"] = "LIGHT"
    partial_taken: bool = False               # scalper partial at +0.5R taken?
    # Optional scratch (HEAVY scalper): if +0.25R not hit within 5 min → move stop to BE
    scratch_after_sec: int = 300


class Trade(BaseModel):
    side: Side
    entry: float
    close: float
    pnl: float
    open_time: int
    close_time: int


class Settings(BaseModel):
    # kept for compatibility; hidden in UI now
    scalp_mode: bool = True
    auto_trade: bool = True
    strategy: str = "Adaptive Router"
    macro_pause: bool = False

    # V2: profile picker
    profile_mode: ProfileMode = "AUTO"


class Status(BaseModel):
    price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    status: str = "Loading..."
    equity: float
    pos: Optional[Position] = None
    history: List[Trade] = Field(default_factory=list)
    candles: List[Candle] = Field(default_factory=list)

    # UI flags (legacy, still emitted; UI ignores toggles)
    scalpMode: bool = True
    autoTrade: bool = True

    # Strategy labels
    strategy: str = "Adaptive Router"          # overall (from settings)
    activeStrategy: Optional[str] = None       # router-selected sub-strategy

    # Telemetry for explanations
    regime: Optional[str] = None               # Range | Trending | Breakout
    bias: Optional[str] = None                 # Bullish | Bearish
    adx: Optional[float] = None
    atrPct: Optional[float] = None             # e.g., 0.0043 (0.43%)
    fillsToday: int = 0
    pnlToday: float = 0.0
    unrealNet: float = 0.0

    # V2 profile telemetry
    profileMode: ProfileMode = "AUTO"          # user setting (AUTO/LIGHT/HEAVY)
    profileModeActive: Literal["LIGHT", "HEAVY"] = "LIGHT"

    # NEW: expose active ATR band so UI can show why ATR gate is blocking
    atrBandMin: Optional[float] = None         # e.g., 0.0004
    atrBandMax: Optional[float] = None         # e.g., 0.0200
