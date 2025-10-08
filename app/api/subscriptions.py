# app/api/subscriptions.py
from __future__ import annotations
from uuid import UUID
from typing import List, Optional
import hashlib, json 

from fastapi import APIRouter, Depends, Header, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.persistence.repo import SubscriptionRepo, IdempotencyRepo
from app.core.deps import get_db, get_stripe_provider
from app.engine.engine import SubscriptionEngine, EngineError
from app.schemas.api_models import (
    CreateSubscriptionRequest,
    SubscriptionResponse,
    ChangePlanRequest,
    CancelRequest,
    ResumeRequest,
)
from app.persistence.repo import SubscriptionRepo

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])

@router.post("", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: CreateSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
    stripe = Depends(get_stripe_provider),
    project_id: str = Header(..., alias="X-Project-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    # ---- Idempotency gate (required by your LLD) ----
    if not idempotency_key:
        raise HTTPException(status_code=400, detail={"type": "missing_header", "message": "Idempotency-Key required"})

    def _stable_request_hash(payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    idem_repo = IdempotencyRepo(db)
    req_hash = _stable_request_hash(body.model_dump(mode="json"))

    existing = await idem_repo.get(project_id=project_id, key=idempotency_key)
    if existing:
        if existing.status == "succeeded" and existing.response:
            return SubscriptionResponse(**existing.response)
        if existing.status == "in_progress":
            raise HTTPException(status_code=409, detail={"type": "in_progress", "message": "Request is being processed"})
        # if failed -> continue to retry

    row = await idem_repo.create_in_progress(project_id=project_id, key=idempotency_key, request_hash=req_hash)

    try:
        engine = SubscriptionEngine(db=db, stripe=stripe, project_id=project_id)
        sub = await engine.create_subscription(body, idempotency_key=idempotency_key)  # pass key down
        await idem_repo.mark_succeeded(row, response_payload=sub.model_dump(mode="json"))
        await db.commit()
        return sub

    except EngineError as e:
        await idem_repo.mark_failed(row, response_payload={"type": "validation_error", "message": str(e)})
        await db.commit()
        raise HTTPException(status_code=400, detail={"type": "validation_error", "message": str(e)})

    except Exception as e:
        await idem_repo.mark_failed(row, response_payload={"type": "error", "message": str(e)})
        await db.commit()
        raise

@router.get("/{subscription_id}", response_model=SubscriptionResponse)
async def get_subscription(
    subscription_id: UUID,
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    sub = await SubscriptionRepo(db).get(subscription_id)
    if not sub or sub.project_id != project_id:
        raise HTTPException(status_code=404, detail={"type":"not_found","message":"Subscription not found"})
    # Map ORM → API (same as your original, but leave checkoutUrl as stored if you keep it)
    return SubscriptionResponse(
        id=sub.id,
        projectId=sub.project_id,
        accountId=sub.account_id,
        planCode=sub.plan_code,
        quantity=sub.quantity,
        status=sub.status,
        configVersionId=sub.config_version_id,
        stripeCustomerId=sub.stripe_customer_id,
        stripeSubscriptionId=sub.stripe_subscription_id,
        currentPeriodStart=sub.current_period_start.isoformat() if sub.current_period_start else None,
        currentPeriodEnd=sub.current_period_end.isoformat() if sub.current_period_end else None,
        trialEndAt=sub.trial_end_at.isoformat() if sub.trial_end_at else None,
        cancelAtPeriodEnd=sub.cancel_at_period_end,
        checkoutUrl=getattr(sub, "checkout_url", None),
        metadata=sub.meta or None,
    )

@router.get("", response_model=List[SubscriptionResponse])
async def list_subscriptions_for_account(
    accountId: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    subs = await SubscriptionRepo(db).list_for_account(project_id, accountId)
    return [
        SubscriptionResponse(
            id=s.id,
            projectId=s.project_id,
            accountId=s.account_id,
            planCode=s.plan_code,
            quantity=s.quantity,
            status=s.status,
            configVersionId=s.config_version_id,
            stripeCustomerId=s.stripe_customer_id,
            stripeSubscriptionId=s.stripe_subscription_id,
            currentPeriodStart=s.current_period_start.isoformat() if s.current_period_start else None,
            currentPeriodEnd=s.current_period_end.isoformat() if s.current_period_end else None,
            trialEndAt=s.trial_end_at.isoformat() if s.trial_end_at else None,      # <-- NEW
            cancelAtPeriodEnd=s.cancel_at_period_end,
            checkoutUrl=getattr(s, "checkout_url", None),
            metadata=s.meta or None,
        )
        for s in subs
    ]

@router.post("/{subscription_id}/change-plan", response_model=SubscriptionResponse)
async def change_plan(
    subscription_id: UUID,
    body: ChangePlanRequest,
    db: AsyncSession = Depends(get_db),
    stripe = Depends(get_stripe_provider),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    sub = await SubscriptionRepo(db).get(subscription_id)
    if not sub or sub.project_id != project_id:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "Subscription not found"})
    engine = SubscriptionEngine(db=db, stripe=stripe, project_id=project_id)
    try:
        return await engine.change_plan(
            subscription=sub,
            new_plan_code=body.planCode,
            quantity=body.quantity,
            proration_behavior=body.prorationBehavior,
        )
    except EngineError as e:
        raise HTTPException(status_code=400, detail={"type": "validation_error", "message": str(e)})

@router.post("/{subscription_id}/cancel", response_model=SubscriptionResponse)
async def cancel_subscription(
    subscription_id: UUID,
    body: CancelRequest,
    db: AsyncSession = Depends(get_db),
    stripe = Depends(get_stripe_provider),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    sub = await SubscriptionRepo(db).get(subscription_id)
    if not sub or sub.project_id != project_id:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "Subscription not found"})
    engine = SubscriptionEngine(db=db, stripe=stripe, project_id=project_id)
    try:
        return await engine.cancel(subscription=sub, at_period_end=bool(body.cancelAtPeriodEnd))
    except EngineError as e:
        raise HTTPException(status_code=400, detail={"type": "validation_error", "message": str(e)})

@router.post("/{subscription_id}/resume", response_model=SubscriptionResponse)
async def resume_subscription(
    subscription_id: UUID,
    body: ResumeRequest,
    db: AsyncSession = Depends(get_db),
    stripe = Depends(get_stripe_provider),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    sub = await SubscriptionRepo(db).get(subscription_id)
    if not sub or sub.project_id != project_id:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "Subscription not found"})
    engine = SubscriptionEngine(db=db, stripe=stripe, project_id=project_id)
    try:
        return await engine.resume(subscription=sub)
    except EngineError as e:
        raise HTTPException(status_code=400, detail={"type": "validation_error", "message": str(e)})