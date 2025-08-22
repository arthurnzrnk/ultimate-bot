"""Base strategy contract for Strategy V3 Dynamic."""

from pydantic import BaseModel
from typing import Literal, Optional

SignalType = Literal["BUY", "SELL", "WAIT"]


class Signal(BaseModel):
    type: SignalType
    reason: str
    stop_dist: float | None = None
    take_dist: float | None = None
    score: float = 0.0
    tf: Optional[Literal["m1", "h1"]] = None  # optional hint to engine


class Strategy:
    name: str = "Base"

    def evaluate(self, ctx: dict) -> Signal:
        raise NotImplementedError
