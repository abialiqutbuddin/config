from __future__ import annotations
from typing import Optional
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.schemas.api_models import (
    UsageEventRequest,
    UsageEventResponse,
    UsageSummaryResponse,
    UsageSummaryItem,
)
from app.persistence.repo import UsageRepo, SubscriptionRepo, ConfigRepo

router = APIRouter(prefix="/usage", tags=["Usage"])

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)

def _parse_iso_or_now(v: Optional[str]) -> datetime:
    if not v:
        return _now_utc()
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail={"type":"validation_error","message":"occurredAt must be ISO8601"})

@router.post("", response_model=UsageEventResponse, status_code=status.HTTP_201_CREATED)
async def record_usage(
    body: UsageEventRequest,
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    """
    Records a single usage event.
    - Idempotent if `sourceId` repeats for (projectId, accountId, metricKey).
    """
    when = _parse_iso_or_now(body.occurredAt)
    repo = UsageRepo(db)
    row = await repo.upsert_event(
        project_id=project_id,
        account_id=body.accountId,
        metric_key=body.metricKey,
        quantity=body.quantity,
        occurred_at=when,
        source_id=body.sourceId,
        meta=body.metadata,
    )
    await db.commit()
    return UsageEventResponse(
        id=row.id,
        projectId=row.project_id,
        accountId=row.account_id,
        metricKey=row.metric_key,
        quantity=float(row.quantity),
        occurredAt=row.occurred_at.astimezone(timezone.utc).isoformat(),
        sourceId=row.source_id,
        metadata=row.meta or None,
    )

@router.get("/summary", response_model=UsageSummaryResponse)
async def usage_summary(
    accountId: str = Query(..., min_length=1),
    start: Optional[str] = Query(None, description="ISO8601 inclusive start; default: subscription.current_period_start"),
    end: Optional[str] = Query(None, description="ISO8601 exclusive end; default: subscription.current_period_end"),
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    """
    Returns aggregated usage totals for the window [start, end).
    If start/end are not provided, derives them from the latest subscription's current period.
    """

    def _parse_optional_iso(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            raise HTTPException(status_code=400, detail={"type": "validation_error", "message": "start/end must be ISO8601"})

    # Parse provided bounds (if any)
    window_start: Optional[datetime] = _parse_optional_iso(start)
    window_end: Optional[datetime] = _parse_optional_iso(end)

    # If missing, derive from latest subscription's current period
    if not window_start or not window_end:
        srepo = SubscriptionRepo(db)
        subs = await srepo.list_for_account(project_id, accountId)
        active_like = [s for s in subs if s.status in ("trialing", "active", "past_due", "pending")]
        sub = (active_like or subs)[0] if (active_like or subs) else None
        if not sub or not sub.current_period_start or not sub.current_period_end:
            raise HTTPException(
                status_code=400,
                detail={"type": "missing_window", "message": "Provide start/end or ensure subscription has current period"},
            )
        window_start = sub.current_period_start.astimezone(timezone.utc)
        window_end = sub.current_period_end.astimezone(timezone.utc)

    # Validate ordering
    if window_start >= window_end:
        raise HTTPException(
            status_code=400,
            detail={"type": "validation_error", "message": "start must be earlier than end"},
        )

    # Aggregate
    urepo = UsageRepo(db)
    rows = await urepo.summarize_window(
        project_id=project_id, account_id=accountId, start=window_start, end=window_end
    )
    items = [UsageSummaryItem(metricKey=mk, total=tot) for mk, tot in rows]

    return UsageSummaryResponse(
        projectId=project_id,
        accountId=accountId,
        windowStart=window_start.isoformat(),
        windowEnd=window_end.isoformat(),
        items=items,
    )