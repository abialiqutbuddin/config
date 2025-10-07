from __future__ import annotations
from typing import Dict, Type
from app.engine.strategies.base import (
    StrategyBundle,
    ProrationStrategy,
    InvoicingStrategy,
    EntitlementStrategy,
    MeteringStrategy,
    SeatStrategy,
)
from app.engine.strategies.entitlements import StaticEntitlement
from app.engine.strategies.proration import LinearProration, NoProration, AlwaysInvoiceProration
from app.engine.strategies.invoicing import AutoCharge, SendInvoice
from app.engine.strategies.metering import MonthlyWindow
from app.engine.strategies.seats import PooledSeats

PRORATION: Dict[str, Type[ProrationStrategy]] = {
    "linear": LinearProration,
    "none": NoProration,
    "always_invoice": AlwaysInvoiceProration,
}

INVOICING: Dict[str, Type[InvoicingStrategy]] = {
    "invoice-on-change": SendInvoice,            # example mapping
    "charge-automatically": AutoCharge,
    "auto": AutoCharge,                          # alias
}

ENTITLEMENT: Dict[str, Type[EntitlementStrategy]] = {
    "static": StaticEntitlement,
}

METERING: Dict[str, Type[MeteringStrategy]] = {
    "monthly-window": MonthlyWindow,
}

SEATS: Dict[str, Type[SeatStrategy]] = {
    "pooled-seats": PooledSeats,
}

def _build_or_default(mapping, key: str, default_cls):
    cls = mapping.get(key) or default_cls
    return cls()

def build_bundle(plan: dict) -> StrategyBundle:
    """
    Reads plan['strategies'] and returns a concrete StrategyBundle.
    Falls back to sensible defaults if missing.
    """
    strategies = (plan.get("strategies") or {})

    proration = _build_or_default(PRORATION, strategies.get("ProrationStrategy"), LinearProration)
    invoicing = _build_or_default(INVOICING, strategies.get("InvoicingStrategy"), AutoCharge)
    entitlement = _build_or_default(ENTITLEMENT, strategies.get("EntitlementStrategy"), StaticEntitlement)
    metering = _build_or_default(METERING, strategies.get("MeteringStrategy"), MonthlyWindow)
    seats = _build_or_default(SEATS, strategies.get("SeatStrategy"), PooledSeats)

    return StrategyBundle(
        proration=proration,
        invoicing=invoicing,
        entitlement=entitlement,
        metering=metering,
        seats=seats,
    )