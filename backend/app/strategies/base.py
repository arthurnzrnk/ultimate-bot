"""Base strategy contract for Strategy V3.4."""

from __future__ import annotations
from pydantic import BaseModel
from typing import Literal, Optional, Dict, Any

SignalType = Literal["BUY", "SELL", "WAIT"]


class Signal(BaseModel):
    type: SignalType
    reason: str
    stop_dist: float | None = None
    take_dist: float | None = None
    score: float = 0.0
    tf: Optional[Literal["m1", "h1"]] = None
    meta: Optional[Dict[str, Any]] = None  # extra fields for execution module


class Strategy:
    name: str = "Base"
    def evaluate(self, ctx: dict) -> Signal:
        raise NotImplementedError
