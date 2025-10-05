import stripe
from fastapi import HTTPException
from app.core.settings import settings

class StripePaymentProvider:
    def __init__(self, api_key: str, webhook_secret: str):
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        stripe.api_key = api_key

    def verify_signature(self, payload: bytes, sig_header: str):
        try:
            return stripe.Webhook.construct_event(payload, sig_header, self.webhook_secret)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    # TODO: Implement create_customer, create_subscription, attach_payment_method, etc.