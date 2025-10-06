from __future__ import annotations
from typing import Dict, Any
from app.engine.strategies.base import SeatStrategy

class PooledSeats(SeatStrategy):
    def included_seats(self, *, plan_features: Dict[str, Any]) -> int:
        seats = plan_features.get("seats")
        try:
            return int(seats) if seats is not None else 0
        except Exception:
            return 0

    def is_within_seats(self, *, used: int, included: int) -> bool:
        return used <= included if included >= 0 else True