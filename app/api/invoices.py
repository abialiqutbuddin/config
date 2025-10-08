from __future__ import annotations
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.persistence.repo import InvoiceRepo
from app.persistence.models import Invoice, InvoiceLine
from app.schemas.api_models import InvoiceListItem, InvoiceDetail, InvoiceLineItem

router = APIRouter(prefix="/invoices", tags=["Invoices"])

def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None

@router.get("", response_model=List[InvoiceListItem])
async def list_invoices(
    accountId: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    items = await InvoiceRepo(db).list_for_account(
        project_id=project_id, account_id=accountId, limit=limit, offset=offset
    )
    return [
        InvoiceListItem(
            id=i.id,
            projectId=i.project_id,
            stripeInvoiceId=i.stripe_invoice_id,
            stripeCustomerId=i.stripe_customer_id,
            stripeSubscriptionId=i.stripe_subscription_id,
            status=i.status,
            currency=i.currency,
            subtotal=float(i.subtotal) if i.subtotal is not None else None,
            total=float(i.total) if i.total is not None else None,
            hostedInvoiceUrl=i.hosted_invoice_url,
            periodStart=_iso(i.period_start),
            periodEnd=_iso(i.period_end),
            createdAt=_iso(i.created_at),
        )
        for i in items
    ]

@router.get("/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(
    invoice_id: UUID,
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    repo = InvoiceRepo(db)
    inv, lines = await repo.get_detail_with_lines(invoice_id)
    if not inv or inv.project_id != project_id:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "Invoice not found"})

    return InvoiceDetail(
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
        periodStart=_iso(inv.period_start),
        periodEnd=_iso(inv.period_end),
        createdAt=_iso(inv.created_at),
        lines=[
            InvoiceLineItem(
                id=l.id,
                lineType=l.line_type,
                featureKey=l.feature_key,
                quantity=float(l.quantity),
                unitPrice=float(l.unit_price),
                amount=float(l.amount),
            )
            for l in lines
        ],
    )