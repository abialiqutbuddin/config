from __future__ import annotations
from typing import Optional, Tuple, Dict, Any
import stripe
from fastapi import HTTPException

_INTERVAL_MAP = {"monthly": "month", "annual": "year"}


class StripePaymentProvider:
    def __init__(self, api_key: str, webhook_secret: str):
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        stripe.api_key = api_key

    # --- resolve price from currency/cadence/price ---
    def resolve_price_id(self, *, currency: str, cadence: str, unit_price: float) -> str:
        interval = _INTERVAL_MAP.get(cadence)
        if not interval:
            raise HTTPException(status_code=400, detail=f"Unsupported cadence '{cadence}'")
        unit_amount = int(round(unit_price * 100))

        # Try to find an existing Price with same attributes (Stripe Price Search)
        try:
            prices = stripe.Price.search(
                query=(
                    "active:'true' "
                    f"AND currency:'{currency.lower()}' "
                    "AND type:'recurring' "
                    f"AND recurring.interval:'{interval}' "
                    f"AND unit_amount:'{unit_amount}'"
                ),
                limit=1,
            )
            if prices and prices.data:
                return prices.data[0].id
        except Exception:
            # Fallback: scan regular list (safe across older API perms)
            for p in stripe.Price.list(active=True, limit=50).auto_paging_iter():
                if (
                    getattr(p, "currency", "").lower() == currency.lower()
                    and getattr(p, "type", "") == "recurring"
                    and getattr(p, "unit_amount", None) == unit_amount
                    and getattr(p, "recurring", {}).get("interval") == interval
                ):
                    return p.id

        # Create a product+price pair for this (currency, cadence, amount)
        product = stripe.Product.create(name=f"{cadence.capitalize()} {currency.upper()} {unit_price:.2f}")
        price = stripe.Price.create(
            unit_amount=unit_amount,
            currency=currency.lower(),
            recurring={"interval": interval},
            product=product.id,
        )
        return price.id

    # --- webhooks/signature ---
    def verify_signature(self, payload: bytes, sig_header: str):
        try:
            return stripe.Webhook.construct_event(payload, sig_header, self.webhook_secret)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    # --- customers ---
    def ensure_customer(
        self, *, account_id: str, project_id: str, email: Optional[str] = None, metadata: Dict[str, Any] = {}
    ) -> str:
        query = f'metadata["project_id"]:"{project_id}" AND metadata["account_id"]:"{account_id}"'
        try:
            res = stripe.Customer.search(query=query, limit=1)
            if res and len(res.data) > 0:
                return res.data[0].id
        except Exception:
            for c in stripe.Customer.list(limit=50).auto_paging_iter():
                md = getattr(c, "metadata", None) or {}
                if md.get("project_id") == project_id and md.get("account_id") == account_id:
                    return c.id

        created = stripe.Customer.create(
            email=email,
            metadata={"project_id": project_id, "account_id": account_id, **(metadata or {})},
        )
        return created.id

    def retrieve_customer(self, customer_id: str) -> Dict[str, Any]:
        c = stripe.Customer.retrieve(customer_id)
        return {"id": c.id, "metadata": dict(getattr(c, "metadata", {}) or {})}

    # --- checkout ---
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
        metadata: Dict[str, Any] = {},
        idempotency_key: Optional[str] = None,
    ) -> Tuple[str, str]:
        params: Dict[str, Any] = {
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [{"price": price_id, "quantity": quantity}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": metadata or {},
            "subscription_data": {"metadata": metadata or {}},
        }
        if trial_days and trial_days > 0:
            params["subscription_data"]["trial_period_days"] = trial_days
        if coupon:
            params["discounts"] = [{"coupon": coupon}]
        session = stripe.checkout.Session.create(**params,idempotency_key=idempotency_key)
        return session.url, session.id

    # --- subscriptions ---
    def create_subscription(
        self,
        *,
        customer_id: str,
        price_id: str,
        quantity: int,
        trial_days: Optional[int] = None,
        coupon: Optional[str] = None,
        collection_method: str = "charge_automatically",
        metadata: Dict[str, Any] = {},
        proration_behavior: str = "create_prorations",
        idempotency_key: Optional[str] = None,        
    ) -> Dict[str, Any]:
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
        sub = stripe.Subscription.create(**sub_params, idempotency_key=idempotency_key)        
        return {
            "id": sub.id,
            "status": sub.status,
            "current_period_start": int(sub.current_period_start) if getattr(sub, "current_period_start", None) else None,
            "current_period_end": int(sub.current_period_end) if getattr(sub, "current_period_end", None) else None,
            "cancel_at_period_end": bool(getattr(sub, "cancel_at_period_end", False)),
            "customer": sub.customer,
            "trial_end": int(sub.trial_end) if getattr(sub, "trial_end", None) else None,
            "metadata": dict(getattr(sub, "metadata", {}) or {}),
        }

    def update_subscription(
        self, *, subscription_id: str, price_id: str, quantity: int, proration_behavior: str = "create_prorations"
    ) -> Dict[str, Any]:
        sub = stripe.Subscription.retrieve(subscription_id)
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
        if at_period_end:
            canceled = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
        else:
            canceled = stripe.Subscription.delete(subscription_id)
        return {
            "id": canceled.id,
            "status": canceled.status,
            "cancel_at_period_end": bool(getattr(canceled, "cancel_at_period_end", False)),
        }

    def resume_subscription(self, *, subscription_id: str) -> Dict[str, Any]:
        sub = stripe.Subscription.retrieve(subscription_id)
        if not getattr(sub, "cancel_at_period_end", False):
            return {"id": sub.id, "status": sub.status, "cancel_at_period_end": False}
        resumed = stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)
        return {"id": resumed.id, "status": resumed.status, "cancel_at_period_end": bool(getattr(resumed, "cancel_at_period_end", False))}

    def retrieve_subscription(self, subscription_id: str) -> Dict[str, Any]:
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