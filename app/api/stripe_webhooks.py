# app/api/stripe_webhooks.py
from __future__ import annotations

from typing import Optional, Any, Dict
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Header, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_stripe_provider
from app.persistence.repo import EventRepo, SubscriptionRepo, InvoiceRepo

router = APIRouter(prefix="/stripe", tags=["Stripe"])


# -------------------- helpers --------------------

def _utc_from_epoch(ts: Optional[int]) -> Optional[datetime]:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def _to_dict(obj: Any) -> Dict[str, Any]:
    """
    Normalize Stripe SDK objects (have .to_dict_recursive()) and plain dicts to a plain dict.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    # Stripe.py resources expose to_dict_recursive()
    to_dict = getattr(obj, "to_dict_recursive", None)
    if callable(to_dict):
        return to_dict()
    # Fallback best-effort
    return dict(obj)


def _require(md: Dict[str, Any], key: str) -> Optional[str]:
    v = None
    if isinstance(md, dict):
        v = md.get(key)
    return v


# -------------------- webhook --------------------

@router.post("/webhook")
async def webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
    provider = Depends(get_stripe_provider),
):
    """
    Handles key events and syncs local DB:
      - checkout.session.completed -> attach Stripe subscription ID to our local subscription
      - invoice.payment_succeeded / invoice.payment_failed -> mirror invoice and refresh local sub
      - customer.subscription.updated / customer.subscription.deleted -> keep local status in sync
    """
    payload = await request.body()

    # In real Stripe mode we require a signature. In fake mode we don't.
    is_fake = provider.__class__.__name__ == "FakeStripeProvider"
    if not is_fake and not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    # Parse/verify event
    event_obj = provider.verify_signature(payload, stripe_signature or "")
    event = _to_dict(event_obj)
    event_id: str = event.get("id")
    event_type: str = event.get("type")
    data_obj = _to_dict(event.get("data", {})).get("object")  # session / invoice / subscription etc.
    obj = _to_dict(data_obj)

    # Basic shape guard
    if not event_id or not event_type or not isinstance(obj, dict):
        raise HTTPException(status_code=400, detail="Malformed webhook event")

    # Record for idempotency/audit; ignore if duplicate
    md = _to_dict(obj.get("metadata") or {})
    project_id = _require(md, "project_id")
    if not project_id:
     project_id = await _infer_project_id_for_event(event_type, obj, provider)
    if not project_id:
        # As a last resort (schema requires NOT NULL), you can reject or bucket into a sentinel.
        # Opting to reject to avoid bad data.
        raise HTTPException(status_code=400, detail="project_id missing and could not be inferred")

    e_repo = EventRepo(db)
    if not await e_repo.record_if_new(
        provider="stripe",
        event_id=event_id,
        project_id=project_id,
        event_type=event_type,
        payload=event,
    ):
        return {"ok": True, "deduped": True}

    s_repo = SubscriptionRepo(db)
    i_repo = InvoiceRepo(db)

    # -------------------- handlers --------------------

    if event_type == "checkout.session.completed":
        # Session should contain our metadata.subscription_local_id and a subscription id
        local_id_str = _require(md, "subscription_local_id")
        session_sub_id = obj.get("subscription")  # str when created

        if local_id_str and session_sub_id:
            from uuid import UUID
            local_sub = await s_repo.get(UUID(local_id_str))
            if local_sub:
                # Use provider wrapper (works for real & fake)
                remote = _to_dict(provider.retrieve_subscription(session_sub_id))
                await s_repo.update_from_stripe(
                    local_sub,
                    stripe_subscription_id=remote.get("id"),
                    status=remote.get("status"),
                    current_period_start=_utc_from_epoch(remote.get("current_period_start")),
                    current_period_end=_utc_from_epoch(remote.get("current_period_end")),
                    trial_end_at=_utc_from_epoch(remote.get("trial_end")),         
                    cancel_at_period_end=bool(remote.get("cancel_at_period_end", False)),
                )
                await db.commit()

    elif event_type in ("invoice.payment_succeeded", "invoice.payment_failed"):
        # Mirror invoice
        inv = obj  # already a dict
        # project id: prefer explicit metadata; else derive via subscription/customer metadata
        proj = project_id or await _project_from_invoice(inv, provider)
        if proj:
            await i_repo.upsert_from_stripe(project_id=proj, inv=inv)

        # Refresh local subscription if we know its stripe id
        sub_id = inv.get("subscription")
        if sub_id:
            remote = _to_dict(provider.retrieve_subscription(sub_id))
            local = await s_repo.get_by_stripe_id(sub_id)
            if local:
                await s_repo.update_from_stripe(
                    local,
                    status=remote.get("status"),
                    current_period_start=_utc_from_epoch(remote.get("current_period_start")),
                    current_period_end=_utc_from_epoch(remote.get("current_period_end")),
                    trial_end_at=_utc_from_epoch(remote.get("trial_end")),   
                    cancel_at_period_end=bool(remote.get("cancel_at_period_end", False)),
                )
        await db.commit()

    elif event_type == "customer.subscription.updated":
        remote = obj
        sub_id = remote.get("id")
        if sub_id:
            local = await s_repo.get_by_stripe_id(sub_id)
            if local:
                await s_repo.update_from_stripe(
                    local,
                    status=remote.get("status"),
                    current_period_start=_utc_from_epoch(remote.get("current_period_start")),
                    current_period_end=_utc_from_epoch(remote.get("current_period_end")),
                    trial_end_at=_utc_from_epoch(remote.get("trial_end")),            # ← add this
                    cancel_at_period_end=bool(remote.get("cancel_at_period_end", False)),
                )
                await db.commit()

    elif event_type == "customer.subscription.deleted":
        remote = obj
        sub_id = remote.get("id")
        if sub_id:
            local = await s_repo.get_by_stripe_id(sub_id)
            if local:
                await s_repo.update_from_stripe(local, status="canceled")
                await db.commit()

    # no-op for other events (you can extend here)
    return {"ok": True, "type": event_type}


# -------------------- helpers for project inference --------------------

async def _project_from_invoice(inv: Dict[str, Any], provider) -> Optional[str]:
    """
    Best-effort project id extraction when invoice lacks explicit metadata.project_id
    — try subscription.metadata then customer.metadata.
    """
    # Try subscription
    try:
        sub_id = inv.get("subscription")
        if sub_id:
            s = _to_dict(provider.retrieve_subscription(sub_id))
            md = _to_dict(s.get("metadata") or {})
            if "project_id" in md:
                return md["project_id"]
    except Exception:
        pass

    # Try customer
    try:
        cus_id = inv.get("customer")
        if cus_id:
            c = _to_dict(provider.retrieve_customer(cus_id))
            md = _to_dict(c.get("metadata") or {})
            if "project_id" in md:
                return md["project_id"]
    except Exception:
        pass

    return None

async def _infer_project_id_for_event(event_type: str, obj: Dict[str, Any], provider) -> Optional[str]:
    """
    Try to infer project_id when metadata.project_id is absent:
      - invoice.*: use existing _project_from_invoice()
      - customer.subscription.*: look at obj.metadata or fetch subscription/customer
      - checkout.session.completed: obj.subscription -> fetch subscription.metadata
    """
    md = _to_dict(obj.get("metadata") or {})
    if "project_id" in md:
        return md["project_id"]

    # invoice.*
    if event_type.startswith("invoice."):
        return await _project_from_invoice(obj, provider)

    # customer.subscription.*
    if event_type.startswith("customer.subscription."):
        sub_id = obj.get("id")
        try:
            s = _to_dict(provider.retrieve_subscription(sub_id)) if sub_id else {}
            md2 = _to_dict(s.get("metadata") or {})
            if "project_id" in md2:
                return md2["project_id"]
            # fallback via customer
            cus_id = s.get("customer")
            if cus_id:
                c = _to_dict(provider.retrieve_customer(cus_id))
                md3 = _to_dict(c.get("metadata") or {})
                if "project_id" in md3:
                    return md3["project_id"]
        except Exception:
            pass

    # checkout.session.completed
    if event_type == "checkout.session.completed":
        sub_id = obj.get("subscription")
        try:
            s = _to_dict(provider.retrieve_subscription(sub_id)) if sub_id else {}
            md2 = _to_dict(s.get("metadata") or {})
            if "project_id" in md2:
                return md2["project_id"]
            cus_id = s.get("customer")
            if cus_id:
                c = _to_dict(provider.retrieve_customer(cus_id))
                md3 = _to_dict(c.get("metadata") or {})
                if "project_id" in md3:
                    return md3["project_id"]
        except Exception:
            pass

    return None