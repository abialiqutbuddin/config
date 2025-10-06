from __future__ import annotations
from app.engine.strategies.base import MeteringStrategy

class MonthlyWindow(MeteringStrategy):
    """
    Default to "sum" for all metrics. You can evolve this to read per-metric rules.
    """
    def aggregation(self, *, metric_key: str) -> str:
        return "sum"