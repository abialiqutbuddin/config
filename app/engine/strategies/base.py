from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

# ---------- Proration ----------

class ProrationStrategy(ABC):
    @abstractmethod
    def proration_behavior(self) -> str:
        """
        Return Stripe-compatible behavior:
          - "create_prorations"
          - "none"
          - "always_invoice"
        """
        raise NotImplementedError

# ---------- Invoicing ----------

class InvoicingStrategy(ABC):
    @abstractmethod
    def collection_method(self) -> str:
        """
        Return Stripe collection method:
          - "charge_automatically"
          - "send_invoice"
        """
        raise NotImplementedError

# ---------- Entitlement ----------

class EntitlementStrategy(ABC):
    @abstractmethod
    def is_entitled(self, *, feature_key: str, now: datetime, context: Dict[str, Any]) -> bool:
        """
        True if the account is entitled to use the feature right now.
        `context` can include plan features, usage totals, seats, etc.
        """
        raise NotImplementedError

# ---------- Metering ----------

class MeteringStrategy(ABC):
    @abstractmethod
    def aggregation(self, *, metric_key: str) -> str:
        """
        How to aggregate usage for a metric (e.g., "sum", "max", "count").
        """
        raise NotImplementedError

# ---------- Seats ----------

class SeatStrategy(ABC):
    @abstractmethod
    def included_seats(self, *, plan_features: Dict[str, Any]) -> int:
        """
        Return number of included seats for a plan.
        """
        raise NotImplementedError

    @abstractmethod
    def is_within_seats(self, *, used: int, included: int) -> bool:
        """
        Validate that used seats are within included seats.
        """
        raise NotImplementedError


# ---------- Bundle ----------

@dataclass(frozen=True)
class StrategyBundle:
    proration: ProrationStrategy
    invoicing: InvoicingStrategy
    entitlement: EntitlementStrategy
    metering: MeteringStrategy
    seats: SeatStrategy