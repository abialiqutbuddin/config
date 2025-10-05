from fastapi import APIRouter, Request, Header, Depends
from app.core.deps import get_stripe_provider

router = APIRouter(prefix="/stripe", tags=["Stripe"])

@router.post("/webhook")
async def webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
    stripe=Depends(get_stripe_provider),
):
    payload = await request.body()
    event = stripe.verify_signature(payload, stripe_signature)
    # TODO: Handle Stripe events
    return {"received": True}