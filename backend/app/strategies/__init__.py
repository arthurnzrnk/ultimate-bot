"""
Strategy V3 registry.

Only V3 router + signal interfaces are exported. Legacy standalone strategy
modules were removed to eliminate dead code and confusion.
"""

from .base import Strategy, Signal
from .router import RouterV3, M1Scalp, H1MeanReversion, H1Breakout, H1Trend

# Back‑compat alias so any legacy imports still resolve to V3.
StrategyRouter = RouterV3

__all__ = [
    "Strategy",
    "Signal",
    "RouterV3",
    "M1Scalp",
    "H1MeanReversion",
    "H1Breakout",
    "H1Trend",
    "StrategyRouter",
]
