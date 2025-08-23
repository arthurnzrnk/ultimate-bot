"""Data models for the Ultimate Bot backend — Strategy V3 Dynamic."""

from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Dict, Any

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
    stop_dist: float                 # 1R in absolute $
    fee_rate: float
    open_time: int
    hi: float
    lo: float
    be: bool = False                 # moved stop to BE?
    tf: Literal["m1", "h1"] = "m1"   # which timeframe opened this
    partial_taken: bool = False      # partial at +0.5R taken?
    scratch_after_sec: int = 240     # used for time‑scratch logic (scalper)
    opened_by: Optional[str] = None  # strategy label
    extra_scaled: bool = False       # RSI extreme extra scale‑out taken?
    meta: Optional[Dict[str, Any]] = None  # telemetry snapshot at open


class Trade(BaseModel):
    side: Side
    entry: float
    close: float
    pnl: float
    open_time: int
    close_time: int
    r_multiple: Optional[float] = None  # realized R at close (pnl / (qty*stop_dist))

    # Optional telemetry fields (for §10 logs; UI table ignores them)
    tf: Optional[str] = None
    strategy: Optional[str] = None
    regime: Optional[str] = None
    vs: Optional[float] = None
    ps: Optional[float] = None
    spread_bps: Optional[float] = None
    slip_est: Optional[float] = None
    fee_to_tp: Optional[float] = None
    score: Optional[float] = None
    vol_multiple: Optional[float] = None
    candle_type: Optional[str] = None


class Status(BaseModel):
    # Prices
    price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None

    # Human status (2–4 words)
    status: str = "Loading..."

    # Account
    equity: float
    pos: Optional[Position] = None
    history: List[Trade] = Field(default_factory=list)

    # Chart
    candles: List[Candle] = Field(default_factory=list)

    # Labels
    strategy: str = "Strategy V3 — Dynamic"
    activeStrategy: Optional[str] = None

    # Market conditions only (for the Conditions line)
    regime: Optional[str] = None          # Range | Trend | Breakout
    bias: Optional[str] = None            # Bullish | Bearish (h1 EMA200)
    adx: Optional[float] = None
    atrPct: Optional[float] = None        # e.g., 0.0043 (0.43%)
    rsiM1: Optional[float] = None
    rsiH1: Optional[float] = None
    macdM1: Optional[str] = None          # up | down | cross | flat
    macdH1: Optional[str] = None

    # Session metrics
    fillsToday: int = 0
    pnlToday: float = 0.0
    unrealNet: float = 0.0

    # Telemetry (not shown in Conditions, but handy)
    vs: Optional[float] = None            # Volatility Score
    ps: Optional[float] = None            # Performance Score
    lossStreak: float = 0.0
    spreadBps: Optional[float] = None
    feeToTp: Optional[float] = None
    slipEst: Optional[float] = None
    top3DepthNotional: Optional[float] = None

    # UI convenience
    autoTrade: bool = False
