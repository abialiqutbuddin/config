# app/payments/fake_provider.py
from __future__ import annotations
from typing import Tuple, Optional, Dict, Any
import json
from datetime import datetime, timedelta, timezone

class FakeStripeProvider:
    """
    In-memory, protocol-compliant fake for tests/local runs.
    Mirrors the signatures in app.payments.types.PaymentProvider.

    - Prices: resolved by (currency, cadence, unit_price) -> stable fake price_id.
    - Customers: keyed by account_id; metadata includes project/account ids.
    - Subscriptions: stored in-memory with Stripe-like fields; supports create/update/cancel/resume.
    - Webhooks: verify_signature just parses JSON (no signature validation).
    """

    def __init__(self):
        # account_id -> {id, metadata}
        self.customers: Dict[str, Dict[str, Any]] = {}
        # sub_id -> dict (Stripe-like shape where needed)
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        # key(currency:cadence:amount) -> price_id
        self._prices: Dict[str, str] = {}
        # simple counters
        self._price_counter: int = 0
        self._session_counter: int = 0
        self._sub_counter: int = 0

    # ----------------------- helpers -----------------------

    @staticmethod
    def _now_ts() -> int:
        # wall-clock now in UTC (epoch seconds)
        return int(datetime.now(tz=timezone.utc).timestamp())

    @staticmethod
    def _price_key(*, currency: str, cadence: str, unit_price: float) -> str:
        return f"{currency.upper()}:{cadence}:{unit_price:.2f}"

    # ------------------ price resolution -------------------

    def resolve_price_id(self, *, currency: str, cadence: str, unit_price: float) -> str:
        key = self._price_key(currency=currency, cadence=cadence, unit_price=unit_price)
        pid = self._prices.get(key)
        if not pid:
            self._price_counter += 1
            pid = f"price_fake_{self._price_counter}"
            self._prices[key] = pid
        return pid

    # ---------------- webhooks / signature -----------------

    def verify_signature(self, payload: bytes, sig_header: str):
        """
        In fake, ignore signature and just parse the payload as JSON.
        Compatible with real handler expecting a dict-like event.
        """
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    # --------------------- customers -----------------------

    def ensure_customer(
        self, *, account_id: str, project_id: str, email: Optional[str], metadata: Dict[str, Any]
    ) -> str:
        entry = self.customers.get(account_id)
        if entry:
            return entry["id"]
        cid = f"cus_test_{len(self.customers) + 1}"
        self.customers[account_id] = {
            "id": cid,
            "email": email,
            "metadata": {"project_id": project_id, "account_id": account_id, **(metadata or {})},
        }
        return cid

    def retrieve_customer(self, customer_id: str) -> Dict[str, Any]:
        for entry in self.customers.values():
            if entry["id"] == customer_id:
                return {"id": customer_id, "metadata": dict(entry.get("metadata") or {})}
        return {"id": customer_id, "metadata": {}}

    # --------------------- checkout ------------------------

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
        idempotency_key: Optional[str] = None,  # accepted & ignored in fake
    ) -> Tuple[str, str]:
        """
        Returns a stable fake URL and a fake session id.
        No persistent session state; real subscription is linked via a webhook in tests.
        """
        self._session_counter += 1
        return ("https://checkout.local/success", f"cs_test_{self._session_counter}")

    # ------------------- subscriptions ---------------------

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
        idempotency_key: Optional[str] = None,  # accepted & ignored in fake
    ) -> Dict[str, Any]:
        self._sub_counter += 1
        sid = f"sub_test_{self._sub_counter}"

        now = self._now_ts()
        # naive 30-day period for fake
        period_end = now + 30 * 24 * 3600

        has_trial = bool(trial_days and int(trial_days) > 0)
        trial_end = now + int(trial_days) * 24 * 3600 if has_trial else None

        sub = {
            "id": sid,
            "customer": customer_id,
            "status": "trialing" if has_trial else "active",
            "current_period_start": now,
            "current_period_end": period_end,
            "cancel_at_period_end": False,
            "collection_method": collection_method,
            "proration_behavior": proration_behavior,
            "metadata": dict(metadata or {}),
            "trial_end": trial_end,
            "items": {
                "data": [
                    {"id": f"si_{sid}_1", "price": price_id, "quantity": quantity}
                ]
            },
        }
        self.subscriptions[sid] = sub
        return dict(sub)

    def update_subscription(
        self, *, subscription_id: str, price_id: str, quantity: int, proration_behavior: str
    ) -> Dict[str, Any]:
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            # create a placeholder so change-plan on unknown id doesn't crash
            now = self._now_ts()
            period_end = now + 30 * 24 * 3600
            sub = {
                "id": subscription_id,
                "customer": "cus_fake",
                "status": "active",
                "current_period_start": now,
                "current_period_end": period_end,
                "cancel_at_period_end": False,
                "trial_end": None,
                "metadata": {},
                "items": {"data": [{"id": f"si_{subscription_id}_1", "price": price_id, "quantity": quantity}]},
            }
            self.subscriptions[subscription_id] = sub

        sub["items"]["data"][0].update({"price": price_id, "quantity": quantity})
        sub["proration_behavior"] = proration_behavior
        return dict(sub)

    def cancel_subscription(self, *, subscription_id: str, at_period_end: bool) -> Dict[str, Any]:
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
            "status": sub.get("status", "canceled"),
            "cancel_at_period_end": bool(sub.get("cancel_at_period_end", False)),
        }

    def resume_subscription(self, *, subscription_id: str) -> Dict[str, Any]:
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

    def retrieve_subscription(self, subscription_id: str) -> Dict[str, Any]:
        sub = self.subscriptions.get(subscription_id)
        if sub:
            return dict(sub)

        # If unknown, return a harmless active shape (mirrors earlier behavior)
        now = self._now_ts()
        period_end = now + 30 * 24 * 3600
        return {
            "id": subscription_id,
            "status": "active",
            "current_period_start": now,
            "current_period_end": period_end,
            "cancel_at_period_end": False,
            "trial_end": None,
            "metadata": {},
            "customer": "cus_fake",
            "items": {"data": [{"id": f"si_{subscription_id}_1", "price": "price_fake_unknown", "quantity": 1}]},
        }