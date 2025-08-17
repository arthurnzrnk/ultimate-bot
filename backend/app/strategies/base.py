"""Base strategy interface for the Ultimate Bot.

Strategy classes encapsulate the logic for generating trade signals on a
given timeframe. Each strategy must implement the ``evaluate`` method,
which returns a ``Signal`` indicating whether to buy, sell or wait.

Strategies are stateless and receive all necessary context via their
``evaluate`` arguments, making them easy to test and swap in the engine.
"""

from pydantic import BaseModel
from typing import Literal

# Define a literal type for signal actions
SignalType = Literal["BUY", "SELL", "WAIT"]


class Signal(BaseModel):
    """Represents a strategy evaluation result."""
    type: SignalType
    reason: str
    stop_dist: float | None = None
    take_dist: float | None = None
    score: int = 0


class Strategy:
    """Interface for all strategies.

    Subclasses must implement the ``evaluate`` method.
    """

    name: str = "Base"

    def evaluate(self, ohlc: list[dict], ctx: dict) -> Signal:
        """Evaluate market data and return a trading signal.

        Args:
            ohlc: List of candle dictionaries.
            ctx: Additional context like current bid/ask, fee rates and
                thresholds.

        Returns:
            A ``Signal`` instance describing the action to take.
        """
        raise NotImplementedError