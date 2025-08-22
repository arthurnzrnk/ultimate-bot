"""
Initialize the strategies package for Strategy V3 Dynamic.

Exports V3 components from app.strategies to avoid import errors and
confusion with older V2 files.
"""

from .strategies.base import Strategy, Signal
from .strategies.router import RouterV3, M1Scalp, H1MeanReversion, H1Breakout, H1Trend

__all__ = [
    "Strategy",
    "Signal",
    "RouterV3",
    "M1Scalp",
    "H1MeanReversion",
    "H1Breakout",
    "H1Trend",
]
