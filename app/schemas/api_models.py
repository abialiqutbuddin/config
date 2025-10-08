from __future__ import annotations

from typing import Optional, Dict, Any, Literal, List
from uuid import UUID
from pydantic import BaseModel, Field, conint


# -------------------------
# Subscriptions
# -------------------------
class CreateSubscriptionRequest(BaseModel):
    accountId: str = Field(..., min_length=1)
    planCode: str = Field(..., min_length=1)
    quantity: int = Field(1, ge=1)
    checkout: Optional[bool] = True                 # True => hosted Checkout; False => direct API
    coupon: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None       # arbitrary round-tripped to provider


class SubscriptionResponse(BaseModel):
    id: UUID
    projectId: str
    accountId: str
    planCode: str
    quantity: int
    status: str
    configVersionId: UUID
    stripeCustomerId: Optional[str] = None
    stripeSubscriptionId: Optional[str] = None
    currentPeriodStart: Optional[str] = None  # ISO8601 or None
    currentPeriodEnd: Optional[str] = None    # ISO8601 or None
    trialEndAt: Optional[str] = None
    cancelAtPeriodEnd: bool = False
    checkoutUrl: Optional[str] = None         # populated on checkout flow
    metadata: Optional[Dict[str, Any]] = None


class ChangePlanRequest(BaseModel):
    planCode: str = Field(..., min_length=1)
    quantity: int = Field(1, ge=1)
    prorationBehavior: Literal["create_prorations", "none", "always_invoice"] = "create_prorations"


class CancelRequest(BaseModel):
    cancelAtPeriodEnd: bool = True


class ResumeRequest(BaseModel):
    # present for symmetry / future options
    pass


# -------------------------
# Usage
# -------------------------
class UsageEventRequest(BaseModel):
    accountId: str = Field(..., min_length=1)
    metricKey: str = Field(..., min_length=1)
    quantity: float = Field(..., gt=0)
    sourceId: Optional[str] = None
    occurredAt: Optional[str] = None         # ISO8601; defaults to now() if missing
    metadata: Optional[Dict[str, Any]] = None


class UsageEventResponse(BaseModel):
    id: UUID
    projectId: str
    accountId: str
    metricKey: str
    quantity: float
    occurredAt: str
    sourceId: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class UsageSummaryItem(BaseModel):
    metricKey: str
    total: float


class UsageSummaryResponse(BaseModel):
    projectId: str
    accountId: str
    windowStart: str
    windowEnd: str
    items: List[UsageSummaryItem]


# -------------------------
# Invoices
# -------------------------
class InvoiceListItem(BaseModel):
    id: UUID
    projectId: str
    stripeInvoiceId: str
    stripeCustomerId: Optional[str] = None
    stripeSubscriptionId: Optional[str] = None
    status: str
    currency: Optional[str] = None
    subtotal: Optional[float] = None
    total: Optional[float] = None
    hostedInvoiceUrl: Optional[str] = None
    periodStart: Optional[str] = None
    periodEnd: Optional[str] = None
    createdAt: Optional[str] = None


# ---------- Invoice Lines ----------
class InvoiceLineItem(BaseModel):
    id: UUID
    lineType: str
    featureKey: Optional[str] = None
    quantity: float
    unitPrice: float
    amount: float

class InvoiceDetail(BaseModel):
    id: UUID
    projectId: str
    stripeInvoiceId: str
    stripeCustomerId: Optional[str] = None
    stripeSubscriptionId: Optional[str] = None
    status: str
    currency: Optional[str] = None
    subtotal: Optional[float] = None
    total: Optional[float] = None
    hostedInvoiceUrl: Optional[str] = None
    periodStart: Optional[str] = None
    periodEnd: Optional[str] = None
    createdAt: Optional[str] = None
    # NEW
    lines: List[InvoiceLineItem] = []

# ---------- Entitlements Cache ----------
class EntitlementsCacheResponse(BaseModel):
    projectId: str
    accountId: str
    asOf: str
    payload: Dict[str, Any]