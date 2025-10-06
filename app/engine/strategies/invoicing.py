from __future__ import annotations
from app.engine.strategies.base import InvoicingStrategy

class AutoCharge(InvoicingStrategy):
    def collection_method(self) -> str:
        return "charge_automatically"

class SendInvoice(InvoicingStrategy):
    def collection_method(self) -> str:
        return "send_invoice"