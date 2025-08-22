"""
Initialize the strategies package for the Ultimate Bot backend.

Keep this module light to avoid circular import headaches.
We re‑export the public strategy interfaces and (optionally) the concrete
strategy classes so callers can import from `app.strategies` *or*
from submodules directly.

Back‑compat:
- `StrategyRouter` is an alias to `RouterV3` (the current router).
"""

from .base import Strategy, Signal

# Optional: expose concrete strategies for convenience
from .level_king_regime import LevelKingRegime
from .mean_reversion import MeanReversion
from .breakout import Breakout
from .trend_follow import TrendFollow

# V3 router + its strategy classes
from .router import RouterV3, M1Scalp, H1MeanReversion, H1Breakout, H1Trend

# Back‑compat alias so legacy imports keep working
StrategyRouter = RouterV3

__all__ = [
    # core interface
    "Strategy",
    "Signal",

    # concrete strategies (optional convenience)
    "LevelKingRegime",
    "MeanReversion",
    "Breakout",
    "TrendFollow",

    # V3 router + components
    "RouterV3",
    "M1Scalp",
    "H1MeanReversion",
    "H1Breakout",
    "H1Trend",

    # legacy alias
    "StrategyRouter",
]
