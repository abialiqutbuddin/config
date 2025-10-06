from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Request, Header, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_stripe_provider
from app.persistence.repo import EventRepo, SubscriptionRepo, InvoiceRepo
from app.engine.engine import EngineError

router = APIRouter(prefix="/stripe", tags=["Stripe"])

def _utc(dt: Optional[int]) -> Optional[datetime]:
    if dt is None:
        return None
    return datetime.fromtimestamp(int(dt), tz=timezone.utc)

@router.post("/webhook")
async def webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
    provider = Depends(get_stripe_provider),
):
    """
    Handles key events and syncs local DB:
    - checkout.session.completed -> attach stripe_sub_id to local sub (via metadata.subscription_local_id)
    - invoice.payment_succeeded / failed -> mirror invoice and refresh subscription status
    - customer.subscription.updated / deleted -> update status/periods/cancel flags
    """
    payload = await request.body()
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    event = provider.verify_signature(payload, stripe_signature)
    event_id = event["id"]
    event_type = event["type"]
    obj = event["data"]["object"]

    # dedupe by event id
    project_id = None
    md = obj.get("metadata") if isinstance(obj, dict) else None
    if isinstance(md, dict):
        project_id = md.get("project_id")

    e_repo = EventRepo(db)
    is_new = await e_repo.record_if_new(
        event_id=event_id,
        project_id=project_id,
        event_type=event_type,
        payload=event,   # store full raw event
    )
    if not is_new:
        return {"ok": True, "deduped": True}

    s_repo = SubscriptionRepo(db)
    i_repo = InvoiceRepo(db)

    # ---- Event handlers ----
    if event_type == "checkout.session.completed":
        # Session contains subscription id and our metadata with local subscription id.
        local_id = md.get("subscription_local_id") if isinstance(md, dict) else None
        session_sub_id = obj.get("subscription")  # may be str
        if local_id and session_sub_id:
            # load local subscription
            from uuid import UUID
            sub = await s_repo.get(UUID(local_id))
            if sub:
                # pull full sub from Stripe for precise status/periods
                remote = stripe.Subscription.retrieve(session_sub_id)
                sub = await s_repo.update_from_stripe(
                    sub,
                    stripe_subscription_id=remote["id"],
                    status=remote["status"],
                    current_period_start=_utc(remote.get("current_period_start")),
                    current_period_end=_utc(remote.get("current_period_end")),
                    cancel_at_period_end=bool(remote.get("cancel_at_period_end")),
                )
        await db.commit()

    elif event_type in ("invoice.payment_succeeded", "invoice.payment_failed"):
        inv = obj  # Stripe invoice
        # mirror invoice
        proj = project_id or _project_from_invoice(inv)  # fallback derivation
        if proj:
            await i_repo.upsert_from_stripe(project_id=proj, inv=inv)
        # refresh the subscription from Stripe (authoritative)
        sub_id = inv.get("subscription")
        if sub_id:
            remote = stripe.Subscription.retrieve(sub_id)
            local = await s_repo.get_by_stripe_id(sub_id)
            if local:
                await s_repo.update_from_stripe(
                    local,
                    status=remote["status"],
                    current_period_start=_utc(remote.get("current_period_start")),
                    current_period_end=_utc(remote.get("current_period_end")),
                    cancel_at_period_end=bool(remote.get("cancel_at_period_end")),
                )
        await db.commit()

    elif event_type == "customer.subscription.updated":
        remote = obj  # Stripe subscription
        sub_id = remote["id"]
        local = await s_repo.get_by_stripe_id(sub_id)
        if local:
            await s_repo.update_from_stripe(
                local,
                status=remote["status"],
                current_period_start=_utc(remote.get("current_period_start")),
                current_period_end=_utc(remote.get("current_period_end")),
                cancel_at_period_end=bool(remote.get("cancel_at_period_end")),
            )
            await db.commit()

    elif event_type == "customer.subscription.deleted":
        remote = obj
        sub_id = remote["id"]
        local = await s_repo.get_by_stripe_id(sub_id)
        if local:
            await s_repo.update_from_stripe(local, status="canceled")
            await db.commit()

    # You can add dispute events etc. later
    return {"ok": True, "type": event_type}

def _project_from_invoice(inv: dict) -> Optional[str]:
    """
    Best-effort project id extraction when metadata is missing.
    We try subscription metadata, then customer metadata.
    """
    try:
        sub_id = inv.get("subscription")
        if sub_id:
            s = stripe.Subscription.retrieve(sub_id)
            md = s.get("metadata") or {}
            return md.get("project_id")
    except Exception:
        pass
    try:
        cus_id = inv.get("customer")
        if cus_id:
            c = stripe.Customer.retrieve(cus_id)
            md = c.get("metadata") or {}
            return md.get("project_id")
    except Exception:
        pass
    return None