"""Application configuration + Spec V3.4 constants."""

from __future__ import annotations
import os
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()


def _get_list(env_key: str) -> list[str]:
    raw = os.getenv(env_key, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


class Settings(BaseModel):
    # Server
    port: int = int(os.getenv("PORT", "8000"))
    cors_origins: list[str] = _get_list("CORS_ORIGINS")

    # Account
    start_equity: float = float(os.getenv("START_EQUITY", "10000"))

    # Datafeed
    use_minimal_feed: bool = str(os.getenv("MINIMAL_FEED", "0")).lower() in ("1", "true", "yes", "on")

    # Synthetic depth until real book wired
    synthetic_top3_notional: float = float(os.getenv("SYN_TOP3_NOTIONAL", "75000"))

    # Venue / ticks
    fee_maker_bps_per_side: float = float(os.getenv("FEE_MAKER_BPS_PER_SIDE", "4"))
    fee_taker_bps_per_side: float = float(os.getenv("FEE_TAKER_BPS_PER_SIDE", "10"))
    assume_taker_exit_on_stops: bool = str(os.getenv("ASSUME_TAKER_EXIT_ON_STOPS", "false")).lower() in ("1","true","yes","on")

    exchange_min_notional: float = float(os.getenv("EXCHANGE_MIN_NOTIONAL", "0"))
    price_tick: float = float(os.getenv("PRICE_TICK", "0"))
    qty_tick: float = float(os.getenv("QTY_TICK", "0"))

    # Exec guards
    spread_cap_bps_m1: float = float(os.getenv("SPREAD_CAP_BPS_M1", "8"))
    slip_coeff_k: float = float(os.getenv("SLIP_COEFF_K", "0.6"))
    top3x_order_notional_min: float = float(os.getenv("TOP3X_ORDER_NOTIONAL_MIN", "2.0"))
    max_shrink_iters: int = int(os.getenv("MAX_SHRINK_ITERS", "12"))
    min_notional_usd: float = float(os.getenv("MIN_NOTIONAL_USD", "0"))

    # ---- Spec constants (unchanging defaults) ----
    class Spec(BaseModel):
        # Market & TFs
        market: str = "BTC-USD"
        tf_m1: int = 60
        tf_h1: int = 3600

        # Fees & bounds
        FEE_TP_MAX_RATIO: float = 0.20
        FAST_TAPE_TAKER_MAX_FEE_TO_TP: float = 0.18

        # ATR / indicators
        ATR_LEN: int = 14
        ADX_LEN: int = 14
        EMA200_LEN_H1: int = 200
        DONCHIAN_LEN: int = 20
        RSI_LEN: int = 14
        MACD_FAST: int = 12
        MACD_SLOW: int = 26
        MACD_SIGNAL: int = 9

        # z‑VWAP
        ZVWAP_STD_WINDOW_M1: int = 40
        Z_MIN: float = 1.0

        # VS/PS
        VS_MIN: float = 0.5
        VS_MAX: float = 2.0
        PS_DECAY_TO: float = 0.5
        PS_DECAY_HOURS_IF_IDLE: int = 2

        # Risk sizing & caps (percents)
        BASE_RISK_PCT_M1: float = 0.8
        BASE_RISK_PCT_H1: float = 0.25
        LIVE_RISK_CAP: float = 1.5
        TOD_RISK_TILT: float = 10.0

        # Partials/BE/trails
        PARTIAL_AT_R: float = 0.60
        PARTIAL_M1_HOTVS_SHIFT: dict = {"VS_GE": 1.2, "PARTIAL_AT_R": 0.70, "PARTIAL_FRACTION": 0.40}
        BE_BUFFER_R: float = 0.10
        TRAIL_R_VS: float = 0.80
        TRAIL_R_TIGHT_ON_MACD_FADE: float = 0.60
        RUNNER_RATCHET_AT_R: float = 1.20
        RUNNER_ACCEL_ENABLE: bool = True
        RUNNER_RATCHET_AT_R_ACCEL: float = 1.10
        RUNNER_ACCEL_MACD_MULT: float = 1.5

        # m1 banding
        SCALPER_ATR_PCT_MIN: float = 0.0005   # 0.05%
        SCALPER_ATR_PCT_MAX: float = 0.0175   # 1.75%
        VWAP_SLOPE_CAP_PCT: float = 0.0005    # × VS
        BAND_PCT_MIN: float = 0.0015          # 0.15%
        BAND_PCT_ATR_MULT: float = 0.75
        TP_PCT_FLOOR: float = 0.0015          # 0.15%
        TP_PCT_FROM_BAND_MULT: float = 0.85

        # Asymmetric TP
        ASYM_M1_ENABLE: bool = True
        ASYM_TP_WIDEN_MULT_BASE: float = 0.25
        A_PLUS_TP_ENABLE: bool = True
        A_PLUS_TP_WIDEN_MULT: float = 0.35
        A_PLUS_GATE_REQ: dict = {"top2_hour": True, "micro_triad": True, "regime_in": ["trend","breakout"], "spread_to_stop_max": 0.04}

        # Cooldowns / day controls
        COOLDOWN_M1_SEC: int = 45
        COOLDOWN_M1_SEC_TOP_HOUR: int = 30
        COOLDOWN_TOP_HOUR_GATE: dict = {"spread_to_stop_max": 0.04, "slip_R_max": 0.20, "VS_range": [0.9,1.4], "PS_min": 0.60}
        COOLDOWN_BONUS_DISABLE_AFTER_LOSS_IN_HOUR: bool = True
        GIVEBACK_PCT_OF_RUNUP: float = 35.0
        GIVEBACK_TIGHT_IF: dict = {"VS_GE": 1.5, "PS_LE": 0.4, "TIGHT_TO": 30.0}

        # Day‑lock
        DAY_LOCK_ENABLE: bool = True
        DAY_LOCK_TRIGGER_PCT: float = 1.00
        DAY_LOCK_GIVEBACK_PCT: float = 20.0
        DAY_LOCK_FLOOR_MIN_PCT: float = 0.60
        DAY_LOCK_ACTION: str = "PAUSE_TO_EOD"

        # Red‑day throttles
        RED_DAY_L1_PCT: float = -1.00
        RED_DAY_L1_RISK_MULT: float = 0.35
        RED_DAY_L1_SCORE_ADD: float = 0.25
        RED_DAY_L1_TOP_HOURS_ONLY: bool = True
        RED_DAY_L2_PCT: float = -2.00
        RED_DAY_L2_HALT_NEW: bool = True

        # Hour gating
        M1_BLOCK_BOTTOM_HOURS: bool = True

        # Fast‑tape
        FAST_TAPE_NEED_MACD_ACCEL: bool = True
        FAST_TAPE_DISABLE_AFTER_FAILS: int = 2
        FAST_TAPE_DISABLE_WINDOW_MIN: int = 30
        FAST_TAPE_DISABLE_COOLDOWN_MIN: int = 60

        # Re‑entry
        REENTRY_MAX_BARS: int = 11

        # Venue/data health
        TICK_LATENCY_WARN_MS: int = 200
        TICK_LATENCY_HALT_MS: int = 500
        ORDER_ACK_P95_MAX_MS: int = 400
        SPREAD_STD_TO_MEDIAN_MAX: float = 1.80
        TOP3_CRUMBLE_MAX_DROP_PCT: float = 0.50

        # Fallback loosening
        FALLBACK_AFTER_HOURS_NO_H1: int = 4
        FALLBACK_ADX_RANGE: tuple[int,int] = (15, 22)
        FALLBACK_VS_DELTA: float = 0.20
        FALLBACK_MAX_ACTIVATIONS_PER_UTC: int = 2

        # Heartbeat & breakers
        HEARTBEAT_MAX_STALL_SEC: int = 5
        HEARTBEAT_PAUSE_MIN: int = 15
        FEE_TP_VIOLATIONS_IN_10M: int = 3
        PAUSE_AFTER_FEE_TP_BREAK_MIN: int = 30
        MACRO_SPIKE_MULT: float = 1.8
        MACRO_PAUSE_MIN: int = 30

    spec: Spec = Spec()


settings = Settings()
