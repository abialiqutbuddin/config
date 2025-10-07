# app/payments/types.py
from __future__ import annotations
from typing import Protocol, Optional, Tuple, Dict, Any

class PaymentProvider(Protocol):
    # --- core / webhooks ---
    def verify_signature(self, payload: bytes, sig_header: str): ...
    def retrieve_subscription(self, subscription_id: str) -> Dict[str, Any]: ...
    def retrieve_customer(self, customer_id: str) -> Dict[str, Any]: ...

    # --- customers ---
    def ensure_customer(
        self, *, account_id: str, project_id: str, email: Optional[str], metadata: Dict[str, Any]
    ) -> str: ...

    # --- checkout ---
    def create_checkout_session(
        self, *, customer_id: str, price_id: str, quantity: int,
        success_url: str, cancel_url: str, trial_days: Optional[int],
        coupon: Optional[str], metadata: Dict[str, Any]
    ) -> Tuple[str, str]: ...

    # --- subscriptions ---
    def create_subscription(
        self, *, customer_id: str, price_id: str, quantity: int,
        trial_days: Optional[int], coupon: Optional[str],
        collection_method: str, proration_behavior: str,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]: ...

    def update_subscription(
        self, *, subscription_id: str, price_id: str, quantity: int, proration_behavior: str
    ) -> Dict[str, Any]: ...

    def cancel_subscription(self, *, subscription_id: str, at_period_end: bool) -> Dict[str, Any]: ...
    def resume_subscription(self, *, subscription_id: str) -> Dict[str, Any]: ...