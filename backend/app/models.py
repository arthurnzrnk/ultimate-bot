"""Data models for Strategy V3.4."""

from __future__ import annotations
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
    stop_dist: float                 # 1R in $
    fee_rate: float                  # applied per side on entry/exit
    open_time: int
    hi: float
    lo: float
    be: bool = False                 # moved stop to BE?
    tf: Literal["m1", "h1"] = "m1"
    partial_taken: bool = False
    scratch_after_sec: int = 240
    opened_by: Optional[str] = None
    extra_scaled: bool = False
    meta: Optional[Dict[str, Any]] = None  # snapshot at open (telemetry)


class Trade(BaseModel):
    side: Side
    entry: float
    close: float
    pnl: float
    open_time: int
    close_time: int
    r_multiple: Optional[float] = None

    # Telemetry (per §11)
    tf: Optional[str] = None
    strategy: Optional[str] = None
    regime: Optional[str] = None
    vs: Optional[float] = None
    ps: Optional[float] = None
    loss_streak: Optional[float] = None  # <-- added earlier in your code

    # Micro
    spread_bps: Optional[float] = None
    spread_std_10s: Optional[float] = None
    spread_median_60s: Optional[float] = None
    top3_notional: Optional[float] = None
    order_notional: Optional[float] = None
    impact_component: Optional[float] = None
    slip_est: Optional[float] = None
    spread_to_stop_ratio: Optional[float] = None
    z_vwap: Optional[float] = None

    # Fees
    assumed_fee_model: Optional[str] = None  # "MM" or "TM"
    round_trip_fee_pct: Optional[float] = None
    fee_to_tp: Optional[float] = None
    tp_fee_floor: Optional[float] = None

    # Targets/stops
    final_stop_dist_R: Optional[float] = None
    final_tp_pct: Optional[float] = None
    entry_price: Optional[float] = None
    tp_price: Optional[float] = None
    stop_price: Optional[float] = None

    # Execution flags
    post_only: Optional[bool] = None
    fast_tape_taker: Optional[int] = None
    crossing_entry: Optional[bool] = None

    # Lifecycle
    partials: Optional[int] = None
    pyramid_adds: Optional[Dict[str, Any]] = None  # {count, size_R: [..], times: [..]}
    trail_events: Optional[int] = None
    win_R: Optional[float] = None
    loss_R: Optional[float] = None
    realized_R: Optional[float] = None
    reject_reason: Optional[str] = None

    # Flags
    asym_m1_on: Optional[int] = None
    day_lock_armed: Optional[int] = None
    day_lock_floor_pct: Optional[float] = None
    red_day_throttle_level: Optional[int] = None
    blocked_bottom_hour: Optional[int] = None
    runner_ratchet_early: Optional[int] = None
    a_plus_gate_on: Optional[int] = None
    fast_tape_disabled: Optional[int] = None
    taker_fail_count_30m: Optional[int] = None
    latency_halt: Optional[int] = None
    fee_breaker_pause: Optional[int] = None
    tick_p95_ms: Optional[float] = None
    order_ack_p95_ms: Optional[float] = None
    spread_instability_block: Optional[int] = None
    top3_notional_drop_pct_3s: Optional[float] = None
    cooldown_bonus_on: Optional[int] = None

    # Naming parity with §11
    realized_slip_R: Optional[float] = None  # paper: typically None

    # Extras kept for UI convenience
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
    strategy: str = "Strategy V3.4"
    activeStrategy: Optional[str] = None

    # Market-only conditions for UI
    regime: Optional[str] = None
    bias: Optional[str] = None
    adx: Optional[float] = None
    atrPct: Optional[float] = None
    rsiM1: Optional[float] = None
    rsiH1: Optional[float] = None
    macdM1: Optional[str] = None
    macdH1: Optional[str] = None

    # Session metrics
    fillsToday: int = 0
    pnlToday: float = 0.0
    unrealNet: float = 0.0

    # Telemetry
    vs: Optional[float] = None
    ps: Optional[float] = None
    lossStreak: float = 0.0
    spreadBps: Optional[float] = None
    feeToTp: Optional[float] = None
    slipEst: Optional[float] = None
    top3DepthNotional: Optional[float] = None

    # Day locks / throttles / taker
    dayLockArmed: Optional[int] = None
    dayLockFloorPct: Optional[float] = None
    redDayLevel: Optional[int] = None
    fastTapeDisabled: Optional[int] = None
    takerFailCount30m: Optional[int] = None

    # UI convenience
    autoTrade: bool = False
