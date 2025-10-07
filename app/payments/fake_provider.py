# app/payments/fake_provider.py
from __future__ import annotations
from typing import Tuple, Optional, Dict, Any
import json
from datetime import datetime, timezone

class FakeStripeProvider:
    def __init__(self):
        self.customers: Dict[str, Dict[str, Any]] = {}        # account_id -> {id, metadata}
        self.subscriptions: Dict[str, Dict[str, Any]] = {}    # sub_id -> dict

    def verify_signature(self, payload: bytes, sig_header: str):
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    # -------- customers --------
    def ensure_customer(self, *, account_id: str, project_id: str, email: Optional[str], metadata: dict) -> str:
        entry = self.customers.get(account_id)
        if entry:
            return entry["id"]
        cid = f"cus_test_{len(self.customers)+1}"
        self.customers[account_id] = {"id": cid, "metadata": {"project_id": project_id, "account_id": account_id, **(metadata or {})}}
        return cid

    # NEW: mirror the real provider’s surface
    def retrieve_customer(self, customer_id: str) -> dict:
        # naive scan (small in-memory store)
        for entry in self.customers.values():
            if entry["id"] == customer_id:
                return {"id": customer_id, "metadata": entry.get("metadata", {})}
        return {"id": customer_id, "metadata": {}}

    # -------- checkout --------
    def create_checkout_session(
        self, *, customer_id: str, price_id: str, quantity: int, success_url: str,
        cancel_url: str, trial_days: int, coupon: Optional[str], metadata: dict
    ) -> Tuple[str, str]:
        return ("https://checkout.local/success", f"cs_test_{len(self.subscriptions)+1}")

    # -------- subscriptions --------
    def create_subscription(
        self, *, customer_id: str, price_id: str, quantity: int, trial_days: int,
        coupon: Optional[str], collection_method: str, proration_behavior: str, metadata: dict
    ) -> dict:
        sid = f"sub_test_{len(self.subscriptions)+1}"
        sub = {
            "id": sid,
            "customer": customer_id,
            "status": "active" if trial_days == 0 else "trialing",
            "current_period_start": 1735689600,
            "current_period_end":   1738281600,
            "cancel_at_period_end": False,
            "collection_method": collection_method,
            "proration_behavior": proration_behavior,
            "metadata": metadata or {},
            "items": {"data": [{"id": f"si_{sid}_1", "price": price_id, "quantity": quantity}]},
        }
        self.subscriptions[sid] = sub
        return sub

    def update_subscription(self, *, subscription_id: str, price_id: str, quantity: int, proration_behavior: str) -> dict:
        sub = self.subscriptions[subscription_id]
        sub["items"]["data"][0].update({"price": price_id, "quantity": quantity})
        sub["proration_behavior"] = proration_behavior
        return sub

    def cancel_subscription(self, *, subscription_id: str, at_period_end: bool) -> dict:
        sub = self.subscriptions[subscription_id]
        if at_period_end:
            sub["cancel_at_period_end"] = True
        else:
            sub["status"] = "canceled"
            sub["cancel_at_period_end"] = False
        return {"id": sub["id"], "status": sub["status"], "cancel_at_period_end": sub["cancel_at_period_end"]}

    def resume_subscription(self, *, subscription_id: str) -> dict:
        sub = self.subscriptions[subscription_id]
        sub["status"] = "active"
        sub["cancel_at_period_end"] = False
        return {"id": sub["id"], "status": sub["status"], "cancel_at_period_end": sub["cancel_at_period_end"]}

    # NEW: mirror the real provider’s surface
    def retrieve_subscription(self, subscription_id: str) -> dict:
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            # return shape compatible with Stripe
            return {
                "id": subscription_id,
                "status": "active",
                "current_period_start": 1735689600,
                "current_period_end": 1738281600,
                "cancel_at_period_end": False,
                "metadata": {},
            }
        return sub