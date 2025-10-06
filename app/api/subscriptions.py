from uuid import UUID
from typing import Optional, List
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db, get_stripe_provider
from app.schemas.api_models import (
    CreateSubscriptionRequest,
    SubscriptionResponse,
    ChangePlanRequest,   # NEW
    CancelRequest,       # NEW
    ResumeRequest,       # NEW
)
from app.engine.engine import SubscriptionEngine, EngineError
from app.engine.engine import SubscriptionEngine
from app.persistence.repo import SubscriptionRepo

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])

@router.post("", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: CreateSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
    stripe = Depends(get_stripe_provider),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    """
    Create a subscription pinned to the latest config for this project.
    Validates planCode. Persists a 'pending' subscription.
    TODO: Wire Stripe to return checkoutUrl or stripeSubscriptionId.
    """
    try:
        engine = SubscriptionEngine(db=db, stripe=stripe, project_id=project_id)
        return await engine.create_subscription(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"type":"validation_error","message":str(e)})

@router.get("/{subscription_id}", response_model=SubscriptionResponse)
async def get_subscription(
    subscription_id: UUID,
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    sub = await SubscriptionRepo(db).get(subscription_id)
    if not sub or sub.project_id != project_id:
        raise HTTPException(status_code=404, detail={"type":"not_found","message":"Subscription not found"})
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
        checkoutUrl=None,
    )

@router.get("", response_model=List[SubscriptionResponse])
async def list_subscriptions_for_account(
    accountId: str,
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
            checkoutUrl=None,
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
    """
    Change plan and/or quantity with proration control.
    prorationBehavior: "create_prorations" | "none" | "always_invoice"
    """
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
    """
    Cancel immediately or at period end.
    """
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
    """
    Resume a subscription that was set to cancel at period end.
    """
    sub = await SubscriptionRepo(db).get(subscription_id)
    if not sub or sub.project_id != project_id:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "Subscription not found"})

    engine = SubscriptionEngine(db=db, stripe=stripe, project_id=project_id)
    try:
        return await engine.resume(subscription=sub)
    except EngineError as e:
        raise HTTPException(status_code=400, detail={"type": "validation_error", "message": str(e)})