from __future__ import annotations
from datetime import datetime
from typing import Dict, Any, List
from app.engine.strategies.base import EntitlementStrategy

class StaticEntitlement(EntitlementStrategy):
    """
    - resolve(): builds a normalized entitlement payload from plan.features
    - compute(): alias to resolve() (back-compat)
    - is_entitled(): your existing boolean checker (left intact)
    """

    # NEW: used by /entitlements/refresh
    def resolve(self, plan_def: Dict[str, Any], overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
        features: List[Dict[str, Any]] = (plan_def or {}).get("features") or []
        out: Dict[str, Any] = {"features": {}}

        for f in features:
            if not isinstance(f, dict):
                continue
            k = f.get("key")
            if not k:
                continue

            # Copy common fields (extend as needed)
            entry: Dict[str, Any] = {}
            for field in ("limit", "included", "overagePrice", "unit", "description"):
                if field in f:
                    entry[field] = f[field]

            # keep any extra custom fields (except "key")
            for kk, vv in f.items():
                if kk not in entry and kk != "key":
                    entry[kk] = vv

            out["features"][k] = entry

        # Optional overrides (e.g. subscription.meta.entitlements / .features)
        if isinstance(overrides, dict):
            ov = overrides.get("entitlements") or overrides.get("features")
            if isinstance(ov, dict):
                for k, v in ov.items():
                    if k in out["features"] and isinstance(v, dict):
                        out["features"][k].update(v)
                    else:
                        out["features"][k] = v

        return out

    # Alias some code paths might use
    def compute(self, plan_def: Dict[str, Any], overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.resolve(plan_def, overrides)

    # Your existing checker (unchanged)
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

        return True