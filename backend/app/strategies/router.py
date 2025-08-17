"""Strategy router.

The router encapsulates the logic for selecting which strategy to use
depending on the current trading mode. In our design, the user toggles
between scalping (1m) and trend following (1h) modes, so the router simply
returns the appropriate strategy instance. To add more complex regime
detection or multi‑strategy allocation, extend this class to incorporate
additional selection logic.
"""

from .level_king_regime import LevelKingRegime
from .trend_follow import TrendFollow


class StrategyRouter:
    """Select a strategy based on the current scalp mode flag."""

    def __init__(self) -> None:
        self.scalp = LevelKingRegime()
        self.trend = TrendFollow()

    def pick(self, mode_scalp: bool):
        """Return the strategy instance corresponding to the mode.

        Args:
            mode_scalp: True if scalping mode is active, False for trend mode.

        Returns:
            The selected ``Strategy`` instance.
        """
        return self.scalp if mode_scalp else self.trend