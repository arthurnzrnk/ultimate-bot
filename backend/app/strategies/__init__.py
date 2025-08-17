"""
Initialize the strategies package for the Ultimate Bot backend.

This package provides modular strategy implementations and the base interface used by 
the trading engine to evaluate market conditions and generate signals.
"""

from .base import Strategy, Signal
from .level_king_regime import LevelKingRegime
from .mean_reversion import MeanReversion
from .breakout import Breakout
from .trend_follow import TrendFollow
from .router import StrategyRouter

__all__ = [
    "Strategy",
    "Signal",
    "LevelKingRegime",
    "MeanReversion",
    "Breakout",
    "TrendFollow",
    "StrategyRouter",
]
