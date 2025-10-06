from __future__ import annotations
from app.engine.strategies.base import ProrationStrategy

class LinearProration(ProrationStrategy):
    def proration_behavior(self) -> str:
        return "create_prorations"

class NoProration(ProrationStrategy):
    def proration_behavior(self) -> str:
        return "none"

class AlwaysInvoiceProration(ProrationStrategy):
    def proration_behavior(self) -> str:
        return "always_invoice"