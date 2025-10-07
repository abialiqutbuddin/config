# app/api/subscriptions.py
from __future__ import annotations
from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

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
):
    try:
        engine = SubscriptionEngine(db=db, stripe=stripe, project_id=project_id)
        sub = await engine.create_subscription(body)

        # If engine decided to go via checkout (according to config.payment.mode), it should
        # put a URL on the returned view-model. If not present, keep None.
        return sub
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"type": "validation_error", "message": str(e)})

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
        cancelAtPeriodEnd=sub.cancel_at_period_end,
        checkoutUrl=getattr(sub, "checkout_url", None),
        metadata=sub.metadata or None if hasattr(sub, "metadata") else None,
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
            cancelAtPeriodEnd=s.cancel_at_period_end,
            checkoutUrl=getattr(s, "checkout_url", None),
            metadata=s.metadata or None if hasattr(s, "metadata") else None,
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