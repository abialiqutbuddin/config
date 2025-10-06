from __future__ import annotations
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession 

from app.core.deps import get_db
from app.persistence.repo import InvoiceRepo
from app.schemas.api_models import InvoiceListItem, InvoiceDetail

router = APIRouter(prefix="/invoices", tags=["Invoices"])

def _fmt(inv) -> InvoiceListItem:
    return InvoiceListItem(
        id=inv.id,
        projectId=inv.project_id,
        stripeInvoiceId=inv.stripe_invoice_id,
        stripeCustomerId=inv.stripe_customer_id,
        stripeSubscriptionId=inv.stripe_subscription_id,
        status=inv.status,
        currency=inv.currency,
        subtotal=float(inv.subtotal) if inv.subtotal is not None else None,
        total=float(inv.total) if inv.total is not None else None,
        hostedInvoiceUrl=inv.hosted_invoice_url,
        periodStart=inv.period_start.isoformat() if inv.period_start else None,
        periodEnd=inv.period_end.isoformat() if inv.period_end else None,
        createdAt=inv.created_at.isoformat() if inv.created_at else None,
    )

@router.get("", response_model=List[InvoiceListItem])
async def list_invoices(
    accountId: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    """
    List invoices for an account.
    Uses mirrored invoices linked via subscriptions for (project_id, account_id).
    """
    repo = InvoiceRepo(db)
    rows = await repo.list_for_account(project_id=project_id, account_id=accountId, limit=limit, offset=offset)
    return [_fmt(r) for r in rows]

@router.get("/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(
    invoice_id: UUID,
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    repo = InvoiceRepo(db)
    inv = await repo.get_by_id(invoice_id)
    if not inv or inv.project_id != project_id:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "Invoice not found"})
    return _fmt(inv)