# app/payments/stripe_provider.py
from __future__ import annotations

from typing import Optional, Tuple, Dict, Any
import stripe
from fastapi import HTTPException


class StripePaymentProvider:
    """
    Real Stripe implementation of the payments provider interface.

    NOTE: Your FakeStripeProvider should mirror the public methods exposed here so
    the rest of the code can switch providers without changes.
    """

    def __init__(self, api_key: str, webhook_secret: str):
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        # Configure the global Stripe client
        stripe.api_key = api_key

    # -------------------------------------------------------------------------
    # Core helpers
    # -------------------------------------------------------------------------
    def verify_signature(self, payload: bytes, sig_header: str):
        """
        Verify and parse a Stripe webhook event. Raises 400 if invalid.
        Returns the parsed event dict/object.
        """
        try:
            return stripe.Webhook.construct_event(payload, sig_header, self.webhook_secret)
        except Exception:
            # Keep it opaque to callers — bad signature means 400.
            raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    # -------------------------------------------------------------------------
    # Customer helpers
    # -------------------------------------------------------------------------
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
        We prefer a search by metadata (fast & exact), falling back to a short list scan
        if search isn't enabled on the account.
        """
        # Try Customer Search first (requires Stripe Search feature).
        query = f'metadata["project_id"]:"{project_id}" AND metadata["account_id"]:"{account_id}"'
        try:
            res = stripe.Customer.search(query=query, limit=1)
            if res and len(res.data) > 0:
                return res.data[0].id
        except Exception:
            # Fallback to list+scan a small page
            for c in stripe.Customer.list(limit=50).auto_paging_iter():
                md = getattr(c, "metadata", None) or {}
                if md.get("project_id") == project_id and md.get("account_id") == account_id:
                    return c.id

        # Create new customer
        created = stripe.Customer.create(
            email=email,
            metadata={
                "project_id": project_id,
                "account_id": account_id,
                **(metadata or {}),
            },
        )
        return created.id

    def retrieve_customer(self, customer_id: str) -> Dict[str, Any]:
        """
        Wrapper used by webhooks/logic to get a consistent dict shape.
        """
        c = stripe.Customer.retrieve(customer_id)
        return {
            "id": c.id,
            "metadata": dict(getattr(c, "metadata", {}) or {}),
        }

    # -------------------------------------------------------------------------
    # Checkout Sessions
    # -------------------------------------------------------------------------
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
        Create a hosted Checkout Session for subscriptions.
        Returns (checkout_url, session_id).
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
            # discounts is the modern way to attach coupons on checkout
            params["discounts"] = [{"coupon": coupon}]

        session = stripe.checkout.Session.create(**params)
        return session.url, session.id

    # -------------------------------------------------------------------------
    # Direct Subscription lifecycle (no hosted checkout)
    # -------------------------------------------------------------------------
    def create_subscription(
        self,
        *,
        customer_id: str,
        price_id: str,
        quantity: int,
        trial_days: Optional[int] = None,
        coupon: Optional[str] = None,
        collection_method: str = "charge_automatically",   # or "send_invoice"
        metadata: Optional[Dict[str, Any]] = None,
        proration_behavior: str = "create_prorations",     # "none" | "always_invoice"
    ) -> Dict[str, Any]:
        """
        Create a subscription immediately via API.
        Returns a small normalized dict.
        """
        items = [{"price": price_id, "quantity": quantity}]
        sub_params: Dict[str, Any] = dict(
            customer=customer_id,
            items=items,
            collection_method=collection_method,
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
            "cancel_at_period_end": bool(getattr(sub, "cancel_at_period_end", False)),
            "customer": sub.customer,
            "metadata": dict(getattr(sub, "metadata", {}) or {}),
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
        Change plan or quantity on an existing subscription.
        Returns a normalized dict with new dates/status.
        """
        sub = stripe.Subscription.retrieve(subscription_id)
        # Replace the first item (single-price subs) with new price/qty
        first_item_id = sub["items"]["data"][0]["id"]
        updated = stripe.Subscription.modify(
            subscription_id,
            items=[{"id": first_item_id, "price": price_id, "quantity": quantity}],
            proration_behavior=proration_behavior,
        )
        return {
            "id": updated.id,
            "status": updated.status,
            "current_period_start": int(updated.current_period_start) if getattr(updated, "current_period_start", None) else None,
            "current_period_end": int(updated.current_period_end) if getattr(updated, "current_period_end", None) else None,
            "cancel_at_period_end": bool(getattr(updated, "cancel_at_period_end", False)),
            "customer": updated.customer,
            "metadata": dict(getattr(updated, "metadata", {}) or {}),
        }

    def cancel_subscription(self, *, subscription_id: str, at_period_end: bool) -> Dict[str, Any]:
        """
        Cancel immediately or schedule cancellation at period end.
        """
        if at_period_end:
            canceled = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
        else:
            canceled = stripe.Subscription.delete(subscription_id)
        return {
            "id": canceled.id,
            "status": canceled.status,
            "cancel_at_period_end": bool(getattr(canceled, "cancel_at_period_end", False)),
            "customer": canceled.customer if hasattr(canceled, "customer") else None,
        }

    def resume_subscription(self, *, subscription_id: str) -> Dict[str, Any]:
        """
        Clear cancel_at_period_end to resume an active subscription at renewal.
        """
        sub = stripe.Subscription.retrieve(subscription_id)
        if not getattr(sub, "cancel_at_period_end", False):
            # Already active / not scheduled to cancel
            return {
                "id": sub.id,
                "status": sub.status,
                "cancel_at_period_end": False,
                "customer": sub.customer,
            }
        resumed = stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)
        return {
            "id": resumed.id,
            "status": resumed.status,
            "cancel_at_period_end": bool(getattr(resumed, "cancel_at_period_end", False)),
            "customer": resumed.customer,
        }

    def retrieve_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """
        Wrapper used by webhooks/logic. Normalizes the object into a dict so the caller
        doesn't depend on Stripe's object semantics.
        """
        sub = stripe.Subscription.retrieve(subscription_id)
        return {
            "id": sub.id,
            "status": sub.status,
            "current_period_start": int(sub.current_period_start) if getattr(sub, "current_period_start", None) else None,
            "current_period_end": int(sub.current_period_end) if getattr(sub, "current_period_end", None) else None,
            "cancel_at_period_end": bool(getattr(sub, "cancel_at_period_end", False)),
            "customer": sub.customer,
            "metadata": dict(getattr(sub, "metadata", {}) or {}),
        }