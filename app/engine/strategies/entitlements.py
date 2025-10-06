from __future__ import annotations
from datetime import datetime
from typing import Dict, Any
from app.engine.strategies.base import EntitlementStrategy

class StaticEntitlement(EntitlementStrategy):
    """
    Simple policy:
    - If plan.features.limits[feature_key] exists and > 0, it's considered entitled.
    - If plan.features.seats exists, entitlement for "seats" checks seats > 0.
    Everything else defaults to True.
    """
    def is_entitled(self, *, feature_key: str, now: datetime, context: Dict[str, Any]) -> bool:
        plan_features = (context.get("plan_features") or {})
        limits = plan_features.get("limits") or {}
        seats = plan_features.get("seats")

        if feature_key == "seats":
            return isinstance(seats, int) and seats > 0

        if feature_key in limits:
            try:
                return float(limits[feature_key]) > 0
            except Exception:
                return False

        # Default allow
        return True