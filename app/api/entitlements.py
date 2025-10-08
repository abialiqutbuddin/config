from __future__ import annotations
from typing import Dict, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.persistence.repo import EntitlementsRepo, SubscriptionRepo, ConfigRepo
from app.schemas.api_models import EntitlementsCacheResponse
from app.engine.strategies.registry import build_bundle  # assuming this builds strategies

router = APIRouter(prefix="/entitlements", tags=["Entitlements"])

def _iso(dt) -> str:
    return dt.astimezone(timezone.utc).isoformat()

@router.get("", response_model=EntitlementsCacheResponse)
async def get_entitlements(
    accountId: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    repo = EntitlementsRepo(db)
    row = await repo.get(project_id=project_id, account_id=accountId)
    if not row:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "No cached entitlements"})
    return EntitlementsCacheResponse(
        projectId=project_id,
        accountId=accountId,
        asOf=_iso(row.as_of),
        payload=row.payload,
    )

@router.post("/refresh", response_model=EntitlementsCacheResponse, status_code=status.HTTP_200_OK)
async def refresh_entitlements(
    accountId: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    """
    Recompute entitlements for the *latest active* subscription for this account.
    Strategy is read from the plan config (EntitlementStrategy).
    """
    # Find the latest subscription for this account (you can refine ordering/status filters)
    subs = await SubscriptionRepo(db).list_for_account(project_id, accountId)
    if not subs:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "No subscriptions for account"})

    sub = subs[0]  # latest by created_at desc in repo
    cfg = await ConfigRepo(db).get_latest(project_id)
    if not cfg or not isinstance(cfg.json, dict):
        raise HTTPException(status_code=400, detail={"type":"validation_error", "message":"Project config missing"})

    # Find plan definition to feed strategy
    plans = cfg.json.get("plans", [])
    plan_def = next((p for p in plans if p.get("code") == sub.plan_code), None)
    if not plan_def:
        raise HTTPException(status_code=400, detail={"type":"validation_error", "message":"Plan not found in config"})

    # Build strategy bundle and resolve entitlements
    bundle = build_bundle(plan_def)
    ent_payload = bundle.entitlement.resolve(plan_def, overrides=sub.meta or None)

    now = datetime.now(tz=timezone.utc)
    row = await EntitlementsRepo(db).upsert(
        project_id=project_id,
        account_id=accountId,
        as_of=now,
        payload=ent_payload,
    )
    await db.commit()

    return EntitlementsCacheResponse(
        projectId=project_id,
        accountId=accountId,
        asOf=_iso(row.as_of),
        payload=row.payload,
    )