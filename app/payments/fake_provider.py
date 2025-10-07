# app/payments/fake_provider.py
from __future__ import annotations

from typing import Tuple, Optional, Dict, Any
import json
import time


class FakeStripeProvider:
    """
    In-memory fake Stripe provider for local/dev use.

    Mirrors the public surface of StripePaymentProvider so the rest of the app
    can swap providers without code changes.

    - Customers are keyed by account_id (stable per project/account).
    - Subscriptions are keyed by subscription_id (e.g., "sub_test_1").
    - Webhook 'verification' is a no-op: we just json.loads(payload).
    - Time fields use epoch seconds to match Stripe's shape.
    """

    def __init__(self):
        # account_id -> {"id": "cus_test_1", "metadata": {...}}
        self.customers: Dict[str, Dict[str, Any]] = {}
        # subscription_id -> subscription dict
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        # simple counters
        self._next_customer = 1
        self._next_subscription = 1
        self._next_session = 1

    # ---------------------------------------------------------------------
    # Core / webhooks
    # ---------------------------------------------------------------------
    def verify_signature(self, payload: bytes, sig_header: str):
        """
        Fake mode: no signature verification. Just parse and return JSON.
        """
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    def retrieve_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """
        Return a normalized dict for a subscription. If it's missing (e.g. app
        restarted), synthesize a minimal 'active' record so dev flows keep going.
        """
        return self._ensure_sub(subscription_id)

    def retrieve_customer(self, customer_id: str) -> Dict[str, Any]:
        """
        Scan the small in-memory store and return a normalized dict.
        """
        for entry in self.customers.values():
            if entry["id"] == customer_id:
                return {"id": customer_id, "metadata": dict(entry.get("metadata") or {})}
        return {"id": customer_id, "metadata": {}}

    # ---------------------------------------------------------------------
    # Customers
    # ---------------------------------------------------------------------
    def ensure_customer(
        self, *, account_id: str, project_id: str, email: Optional[str], metadata: Dict[str, Any]
    ) -> str:
        """
        Ensure a single customer per (project/account). We key by account_id for dev.
        """
        existing = self.customers.get(account_id)
        if existing:
            return existing["id"]
        cid = f"cus_test_{self._next_customer}"
        self._next_customer += 1
        self.customers[account_id] = {
            "id": cid,
            "metadata": {"project_id": project_id, "account_id": account_id, **(metadata or {})},
        }
        return cid

    # ---------------------------------------------------------------------
    # Checkout
    # ---------------------------------------------------------------------
    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        quantity: int,
        success_url: str,
        cancel_url: str,
        trial_days: Optional[int],
        coupon: Optional[str],
        metadata: Dict[str, Any],
    ) -> Tuple[str, str]:
        """
        Return a fake hosted URL and a fake session id. The webhook flow in dev
        will typically be simulated by hitting /stripe/webhook manually.
        """
        sid = f"cs_test_{self._next_session}"
        self._next_session += 1
        # We don't store sessions; only need to emulate the shape for callers.
        return "https://checkout.local/success", sid

    # ---------------------------------------------------------------------
    # Subscriptions
    # ---------------------------------------------------------------------
    def create_subscription(
        self,
        *,
        customer_id: str,
        price_id: str,
        quantity: int,
        trial_days: Optional[int],
        coupon: Optional[str],
        collection_method: str,
        proration_behavior: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Create a subscription immediately (no hosted checkout).
        """
        sub_id = f"sub_test_{self._next_subscription}"
        self._next_subscription += 1

        now = int(time.time())
        # 30-day 'period' window for dev purposes
        start = now
        end = now + 30 * 24 * 3600

        status = "trialing" if (trial_days or 0) > 0 else "active"
        record = {
            "id": sub_id,
            "customer": customer_id,
            "status": status,
            "current_period_start": start,
            "current_period_end": end,
            "cancel_at_period_end": False,
            "collection_method": collection_method,          # "charge_automatically" | "send_invoice"
            "proration_behavior": proration_behavior,        # "create_prorations" | "none" | "always_invoice"
            "metadata": dict(metadata or {}),
            "items": {
                "data": [
                    {"id": f"si_{sub_id}_1", "price": price_id, "quantity": int(quantity)},
                ]
            },
        }
        self.subscriptions[sub_id] = record
        return self._normalize(record)

    def update_subscription(
        self,
        *,
        subscription_id: str,
        price_id: str,
        quantity: int,
        proration_behavior: str,
    ) -> Dict[str, Any]:
        """
        Update price/quantity and proration behavior.
        """
        sub = self._ensure_sub(subscription_id)
        # keep first item shape like Stripe single-price subs
        if "items" not in sub:
            sub["items"] = {"data": [{"id": f"si_{subscription_id}_1", "price": price_id, "quantity": int(quantity)}]}
        else:
            if not sub["items"].get("data"):
                sub["items"]["data"] = [{"id": f"si_{subscription_id}_1", "price": price_id, "quantity": int(quantity)}]
            else:
                sub["items"]["data"][0].update({"price": price_id, "quantity": int(quantity)})
        sub["proration_behavior"] = proration_behavior
        return self._normalize(sub)

    def cancel_subscription(self, *, subscription_id: str, at_period_end: bool) -> Dict[str, Any]:
        """
        Cancel immediately or schedule cancellation at period end.
        """
        sub = self._ensure_sub(subscription_id)
        if at_period_end:
            sub["cancel_at_period_end"] = True
        else:
            sub["status"] = "canceled"
            sub["cancel_at_period_end"] = False
        return {
            "id": sub["id"],
            "status": sub["status"],
            "cancel_at_period_end": bool(sub.get("cancel_at_period_end", False)),
            "customer": sub.get("customer"),
        }

    def resume_subscription(self, *, subscription_id: str) -> Dict[str, Any]:
        """
        Clear cancel_at_period_end; mark active.
        """
        sub = self._ensure_sub(subscription_id)
        sub["status"] = "active"
        sub["cancel_at_period_end"] = False
        return {
            "id": sub["id"],
            "status": sub["status"],
            "cancel_at_period_end": bool(sub.get("cancel_at_period_end", False)),
            "customer": sub.get("customer"),
        }

    # ---------------------------------------------------------------------
    # Internals (dev convenience)
    # ---------------------------------------------------------------------
    def _ensure_sub(self, subscription_id: str) -> Dict[str, Any]:
        """
        Dev-friendly guard: if a subscription ID isn't known (e.g. server restarted
        between create and change-plan), synthesize a minimal active record to
        avoid KeyError during local testing.
        """
        sub = self.subscriptions.get(subscription_id)
        if sub:
            return sub

        now = int(time.time())
        sub = {
            "id": subscription_id,
            "customer": None,
            "status": "active",
            "current_period_start": now,
            "current_period_end": now + 30 * 24 * 3600,
            "cancel_at_period_end": False,
            "collection_method": "charge_automatically",
            "proration_behavior": "create_prorations",
            "metadata": {},
            "items": {"data": [{"id": f"si_{subscription_id}_1", "price": "price_dev", "quantity": 1}]},
        }
        self.subscriptions[subscription_id] = sub
        return sub

    @staticmethod
    def _normalize(sub: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a normalized subset used by the engine/webhooks to mirror the
        StripePaymentProvider return shape.
        """
        return {
            "id": sub["id"],
            "status": sub.get("status", "active"),
            "current_period_start": int(sub.get("current_period_start")) if sub.get("current_period_start") else None,
            "current_period_end": int(sub.get("current_period_end")) if sub.get("current_period_end") else None,
            "cancel_at_period_end": bool(sub.get("cancel_at_period_end", False)),
            "customer": sub.get("customer"),
            "metadata": dict(sub.get("metadata") or {}),
        }