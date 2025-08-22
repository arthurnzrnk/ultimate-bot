"""
Initialize the strategies package for Strategy V3 Dynamic.

Exports only the V3 components to avoid import errors and confusion with
older V2 files.
"""

from .base import Strategy, Signal
from .router import RouterV3, M1Scalp, H1MeanReversion, H1Breakout, H1Trend

__all__ = [
    "Strategy",
    "Signal",
    "RouterV3",
    "M1Scalp",
    "H1MeanReversion",
    "H1Breakout",
    "H1Trend",
]
