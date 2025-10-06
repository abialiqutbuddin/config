# app/payments/stripe_provider.py
from __future__ import annotations
from typing import Optional, Tuple, Dict, Any
import stripe
from fastapi import HTTPException

class StripePaymentProvider:
    def __init__(self, api_key: str, webhook_secret: str):
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        stripe.api_key = api_key

    # ---------- Core helpers ----------
    def verify_signature(self, payload: bytes, sig_header: str):
        try:
            return stripe.Webhook.construct_event(payload, sig_header, self.webhook_secret)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    # ---------- Customer ----------
    def ensure_customer(
        self,
        *,
        account_id: str,
        project_id: str,
        email: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Ensure a Stripe Customer exists for (project_id, account_id).
        We use a stable metadata pair to find or create.
        """
        # First try to find an existing by search (requires Search beta enabled; if not, fallback to list+filter)
        query = f'metadata["project_id"]:"{project_id}" AND metadata["account_id"]:"{account_id}"'
        try:
            # Search is the most reliable and O(1) feel
            res = stripe.Customer.search(query=query, limit=1)
            if res and len(res.data) > 0:
                return res.data[0].id
        except Exception:
            # Fallback: soft-scan first page
            res = stripe.Customer.list(limit=50)
            for c in res.auto_paging_iter():
                if getattr(c, "metadata", None):
                    if c.metadata.get("project_id") == project_id and c.metadata.get("account_id") == account_id:
                        return c.id

        # Create new
        created = stripe.Customer.create(
            email=email,
            metadata={
                "project_id": project_id,
                "account_id": account_id,
                **(metadata or {}),
            },
        )
        return created.id

    # ---------- Checkout session ----------
    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        quantity: int,
        success_url: str,
        cancel_url: str,
        trial_days: Optional[int] = None,
        coupon: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """
        Returns (checkout_url, session_id).
        Subscription is created after the session completes. Webhook must handle completion.
        """
        params: Dict[str, Any] = {
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [{"price": price_id, "quantity": quantity}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": metadata or {},
            "subscription_data": {
                "metadata": metadata or {},
            },
        }
        if trial_days and trial_days > 0:
            params["subscription_data"]["trial_period_days"] = trial_days
        if coupon:
            # Stripe: discounts param is the modern way
            params["discounts"] = [{"coupon": coupon}]

        session = stripe.checkout.Session.create(**params)
        return session.url, session.id

    # ---------- Direct subscription ----------
    def create_subscription(
        self,
        *,
        customer_id: str,
        price_id: str,
        quantity: int,
        trial_days: Optional[int] = None,
        coupon: Optional[str] = None,
        collection_method: str = "charge_automatically",
        metadata: Optional[Dict[str, Any]] = None,
        proration_behavior: str = "create_prorations",
    ) -> Dict[str, Any]:
        """
        Creates a Subscription immediately (no hosted Checkout).
        Returns a dict with relevant fields.
        """
        items = [{"price": price_id, "quantity": quantity}]
        sub_params: Dict[str, Any] = dict(
            customer=customer_id,
            items=items,
            collection_method=collection_method,  # "charge_automatically" | "send_invoice"
            proration_behavior=proration_behavior,
            metadata=metadata or {},
        )
        if trial_days and trial_days > 0:
            sub_params["trial_period_days"] = trial_days
        if coupon:
            sub_params["discounts"] = [{"coupon": coupon}]

        sub = stripe.Subscription.create(**sub_params)
        return {
            "id": sub.id,
            "status": sub.status,
            "current_period_start": int(sub.current_period_start) if getattr(sub, "current_period_start", None) else None,
            "current_period_end": int(sub.current_period_end) if getattr(sub, "current_period_end", None) else None,
            "customer": sub.customer,
        }

    def update_subscription(
        self,
        *,
        subscription_id: str,
        price_id: str,
        quantity: int,
        proration_behavior: str = "create_prorations",
    ) -> Dict[str, Any]:
        """
        Change plan or quantity. Returns updated sub dates/status.
        """
        sub = stripe.Subscription.retrieve(subscription_id)
        # Replace first item with new price/qty
        items = [{"id": sub["items"]["data"][0]["id"], "price": price_id, "quantity": quantity}]
        updated = stripe.Subscription.modify(
            subscription_id,
            items=items,
            proration_behavior=proration_behavior,
        )
        return {
            "id": updated.id,
            "status": updated.status,
            "current_period_start": int(updated.current_period_start) if getattr(updated, "current_period_start", None) else None,
            "current_period_end": int(updated.current_period_end) if getattr(updated, "current_period_end", None) else None,
            "customer": updated.customer,
        }

    def cancel_subscription(self, *, subscription_id: str, at_period_end: bool) -> Dict[str, Any]:
        if at_period_end:
            canceled = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
        else:
            canceled = stripe.Subscription.delete(subscription_id)
        return {"id": canceled.id, "status": canceled.status, "cancel_at_period_end": bool(canceled.cancel_at_period_end)}

    def resume_subscription(self, *, subscription_id: str) -> Dict[str, Any]:
        sub = stripe.Subscription.retrieve(subscription_id)
        if not sub.get("cancel_at_period_end"):
            # nothing to resume
            return {"id": sub.id, "status": sub.status, "cancel_at_period_end": False}
        resumed = stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)
        return {"id": resumed.id, "status": resumed.status, "cancel_at_period_end": bool(resumed.cancel_at_period_end)}