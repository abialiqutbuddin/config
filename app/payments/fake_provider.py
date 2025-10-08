from __future__ import annotations
from typing import Tuple, Optional, Dict, Any
import json
from datetime import datetime, timezone

_INTERVAL_MAP = {"monthly": "month", "annual": "year"}


class FakeStripeProvider:
    """
    In-memory, protocol-compliant fake for tests/local runs.
    Matches app.payments.types.PaymentProvider signatures.
    """

    def __init__(self):
        # account_id -> {id, metadata}
        self.customers: Dict[str, Dict[str, Any]] = {}
        # sub_id -> dict (Stripe-like shape where needed)
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        # key(currency:cadence:amount) -> price_id
        self._prices: Dict[str, str] = {}

    # ------- helpers -------
    def _price_key(self, *, currency: str, cadence: str, unit_price: float) -> str:
        return f"{currency.upper()}:{cadence}:{unit_price:.2f}"

    # --- resolve price id from currency/cadence/amount ---
    def resolve_price_id(self, *, currency: str, cadence: str, unit_price: float) -> str:
        key = self._price_key(currency=currency, cadence=cadence, unit_price=unit_price)
        pid = self._prices.get(key)
        if not pid:
            pid = f"price_fake_{len(self._prices)+1}"
            self._prices[key] = pid
        return pid

    # --- webhooks/signature ---
    def verify_signature(self, payload: bytes, sig_header: str):
        """
        In fake, no signature check. Accept raw JSON payloads.
        """
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    # --- customers ---
    def ensure_customer(
        self, *, account_id: str, project_id: str, email: Optional[str], metadata: Dict[str, Any]
    ) -> str:
        entry = self.customers.get(account_id)
        if entry:
            return entry["id"]
        cid = f"cus_test_{len(self.customers)+1}"
        self.customers[account_id] = {
            "id": cid,
            "metadata": {"project_id": project_id, "account_id": account_id, **(metadata or {})},
        }
        return cid

    def retrieve_customer(self, customer_id: str) -> dict:
        for entry in self.customers.values():
            if entry["id"] == customer_id:
                return {"id": customer_id, "metadata": entry.get("metadata", {})}
        return {"id": customer_id, "metadata": {}}

    # --- checkout ---
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
        Just return a stable fake URL & a fake session id. No state is kept;
        real subscription will be stitched via a subsequent fake webhook.
        """
        return ("https://checkout.local/success", f"cs_test_{len(self.subscriptions)+1}")

    # --- subscriptions ---
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
    ) -> dict:
        sid = f"sub_test_{len(self.subscriptions)+1}"
        # Fixed example period (30d). You can compute from now() if you prefer.
        now = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
        plus_30d = now + 30 * 24 * 3600
        sub = {
            "id": sid,
            "customer": customer_id,
            "status": "active" if not trial_days or trial_days == 0 else "trialing",
            "current_period_start": now,
            "current_period_end": plus_30d,
            "cancel_at_period_end": False,
            "collection_method": collection_method,
            "proration_behavior": proration_behavior,
            "metadata": metadata or {},
            "items": {"data": [{"id": f"si_{sid}_1", "price": price_id, "quantity": quantity}]},
        }
        self.subscriptions[sid] = sub
        return sub

    def update_subscription(
        self, *, subscription_id: str, price_id: str, quantity: int, proration_behavior: str
    ) -> dict:
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            # create a placeholder so change-plan on unknown id doesn't 500 in fake mode
            now = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
            plus_30d = now + 30 * 24 * 3600
            sub = {
                "id": subscription_id,
                "customer": "cus_fake",
                "status": "active",
                "current_period_start": now,
                "current_period_end": plus_30d,
                "cancel_at_period_end": False,
                "items": {"data": [{"id": f"si_{subscription_id}_1", "price": price_id, "quantity": quantity}]},
                "metadata": {},
            }
            self.subscriptions[subscription_id] = sub

        sub["items"]["data"][0].update({"price": price_id, "quantity": quantity})
        sub["proration_behavior"] = proration_behavior
        return sub

    def cancel_subscription(self, *, subscription_id: str, at_period_end: bool) -> dict:
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            sub = {"id": subscription_id, "status": "canceled", "cancel_at_period_end": False}
            self.subscriptions[subscription_id] = sub
        if at_period_end:
            sub["cancel_at_period_end"] = True
        else:
            sub["status"] = "canceled"
            sub["cancel_at_period_end"] = False
        return {
            "id": sub["id"],
            "status": sub["status"],
            "cancel_at_period_end": sub["cancel_at_period_end"],
        }

    def resume_subscription(self, *, subscription_id: str) -> dict:
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            sub = {"id": subscription_id, "status": "active", "cancel_at_period_end": False}
            self.subscriptions[subscription_id] = sub
        sub["status"] = "active"
        sub["cancel_at_period_end"] = False
        return {
            "id": sub["id"],
            "status": sub["status"],
            "cancel_at_period_end": sub["cancel_at_period_end"],
        }

    def retrieve_subscription(self, subscription_id: str) -> dict:
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            now = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
            plus_30d = now + 30 * 24 * 3600
            return {
                "id": subscription_id,
                "status": "active",
                "current_period_start": now,
                "current_period_end": plus_30d,
                "cancel_at_period_end": False,
                "metadata": {},
            }
        return sub