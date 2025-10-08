from __future__ import annotations
from typing import Optional, List
from uuid import UUID
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import (
    ConfigVersion,
    EntitlementsCache,
    IdempotencyKey,
    InvoiceLine,
    Subscription,
    PaymentEvent,
    Invoice,
    UsageRecord,
)

# -------------------- Configs --------------------

class ConfigRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, *, project_id: str, version_label: str, json_data: dict) -> ConfigVersion:
        # Friendly 409 instead of DB error if duplicate
        existing = await self.get_by_label(project_id, version_label)
        if existing:
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "conflict",
                    "message": f"Config version_label '{version_label}' already exists for project '{project_id}'",
                },
            )
        row = ConfigVersion(project_id=project_id, version_label=version_label, json=json_data)
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def get_by_id(self, version_id: UUID) -> Optional[ConfigVersion]:
        res = await self.db.execute(select(ConfigVersion).where(ConfigVersion.id == version_id))
        return res.scalar_one_or_none()

    async def get_by_label(self, project_id: str, version_label: str) -> Optional[ConfigVersion]:
        res = await self.db.execute(
            select(ConfigVersion).where(
                ConfigVersion.project_id == project_id,
                ConfigVersion.version_label == version_label,
            )
        )
        return res.scalar_one_or_none()

    async def get_latest(self, project_id: str) -> Optional[ConfigVersion]:
        stmt = (
            select(ConfigVersion)
            .where(ConfigVersion.project_id == project_id)
            .order_by(ConfigVersion.created_at.desc())
            .limit(1)
        )
        res = await self.db.execute(stmt)
        return res.scalar_one_or_none()

    async def list_versions(self, project_id: str, limit: int = 20, offset: int = 0) -> List[ConfigVersion]:
        res = await self.db.execute(
            select(ConfigVersion)
            .where(ConfigVersion.project_id == project_id)
            .order_by(ConfigVersion.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(res.scalars().all())


# -------------------- Subscriptions --------------------

class SubscriptionRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **kwargs) -> Subscription:
        row = Subscription(**kwargs)
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def get(self, sub_id: UUID) -> Optional[Subscription]:
        res = await self.db.execute(select(Subscription).where(Subscription.id == sub_id))
        return res.scalar_one_or_none()

    async def list_for_account(self, project_id: str, account_id: str) -> List[Subscription]:
        res = await self.db.execute(
            select(Subscription)
            .where(
                Subscription.project_id == project_id,
                Subscription.account_id == account_id,
            )
            .order_by(Subscription.created_at.desc())
        )
        return list(res.scalars().all())

    async def update(
        self,
        sub: Subscription,
        *,
        plan_code: Optional[str] = None,
        quantity: Optional[int] = None,
        status: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
        current_period_start: Optional[datetime] = None,
        current_period_end: Optional[datetime] = None,
        trial_end_at: Optional[datetime] = None,   # <--- NEW
        cancel_at_period_end: Optional[bool] = None,
        currency: Optional[str] = None,
        unit_price: Optional[float] = None,
        checkout_url: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> Subscription:
        if plan_code is not None:
            sub.plan_code = plan_code
        if quantity is not None:
            sub.quantity = quantity
        if status is not None:
            sub.status = status
        if stripe_subscription_id is not None:
            sub.stripe_subscription_id = stripe_subscription_id
        if current_period_start is not None:
            sub.current_period_start = current_period_start
        if current_period_end is not None:
            sub.current_period_end = current_period_end
        if trial_end_at is not None:                     # <--- NEW
            sub.trial_end_at = trial_end_at
        if cancel_at_period_end is not None:
            sub.cancel_at_period_end = cancel_at_period_end
        if currency is not None:
            sub.currency = currency
        if unit_price is not None:
            sub.unit_price = unit_price
        if checkout_url is not None:
            sub.checkout_url = checkout_url
        if meta is not None:
            sub.meta = meta
        await self.db.flush()
        await self.db.refresh(sub)
        return sub

    async def get_by_stripe_id(self, stripe_subscription_id: str) -> Optional[Subscription]:
        res = await self.db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
        )
        return res.scalar_one_or_none()

    async def update_from_stripe(
        self,
        sub: Subscription,
        *,
        status: Optional[str] = None,
        current_period_start: Optional[datetime] = None,
        current_period_end: Optional[datetime] = None,
        trial_end_at: Optional[datetime] = None,       # <--- NEW
        cancel_at_period_end: Optional[bool] = None,
        stripe_subscription_id: Optional[str] = None,
    ) -> Subscription:
        if status is not None:
            sub.status = status
        if current_period_start is not None:
            sub.current_period_start = current_period_start
        if current_period_end is not None:
            sub.current_period_end = current_period_end
        if trial_end_at is not None:                   # <--- NEW
            sub.trial_end_at = trial_end_at
        if cancel_at_period_end is not None:
            sub.cancel_at_period_end = cancel_at_period_end
        if stripe_subscription_id is not None:
            sub.stripe_subscription_id = stripe_subscription_id
        await self.db.flush()
        await self.db.refresh(sub)
        return sub


# -------------------- Stripe Events (idempotency/audit) --------------------

class EventRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_if_new(
        self,
        *,
        provider: str,
        event_id: str,          # provider's event id (Stripe's 'id')
        project_id: str,        # must be non-null now (per SQL)
        event_type: str,
        payload: dict,
    ) -> bool:
        """
        Insert payment_event if not already recorded.
        Dedupe by (provider, payload->>'id') for Stripe without needing an extra column.
        """
        # EXISTS ... WHERE provider = ? AND payload->>'id' = :event_id
        exists_stmt = (
            select(PaymentEvent.id)
            .where(
                PaymentEvent.provider == provider,
                PaymentEvent.payload["id"].astext == event_id,   # JSONB field
            )
            .limit(1)
        )
        res = await self.db.execute(exists_stmt)
        if res.scalar_one_or_none():
            return False

        self.db.add(
            PaymentEvent(
                project_id=project_id,
                provider=provider,
                event_type=event_type,
                payload=payload,
            )
        )
        await self.db.flush()
        return True


# -------------------- Invoices (mirror from Stripe) --------------------
class InvoiceRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_from_stripe(self, *, project_id: str, inv: dict, mirror_lines: bool = True) -> Invoice:
        """
        Insert or update a mirrored invoice row from a Stripe invoice object,
        and optionally mirror invoice lines.
        """
        stripe_id = inv["id"]
        # Map Stripe sub id to local subscription_id
        stripe_sub_id = inv.get("subscription")
        local_sub_id = None
        if stripe_sub_id:
            sub_q = await self.db.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
            )
            sub_row = sub_q.scalar_one_or_none()
            if sub_row:
                local_sub_id = sub_row.id

        res = await self.db.execute(select(Invoice).where(Invoice.stripe_invoice_id == stripe_id))
        row = res.scalar_one_or_none()

        period_start = _from_epoch(inv.get("period_start"))
        period_end = _from_epoch(inv.get("period_end"))

        if row:
            row.status = inv.get("status") or row.status
            row.subtotal = _maybe_num(inv.get("subtotal"))
            row.total = _maybe_num(inv.get("total"))
            row.currency = inv.get("currency") or row.currency
            row.hosted_invoice_url = inv.get("hosted_invoice_url") or row.hosted_invoice_url
            row.period_start = period_start
            row.period_end = period_end
            if local_sub_id and row.subscription_id != local_sub_id:
                row.subscription_id = local_sub_id
            row.stripe_customer_id = inv.get("customer") or row.stripe_customer_id
            row.stripe_subscription_id = stripe_sub_id or row.stripe_subscription_id
        else:
            row = Invoice(
                project_id=project_id,
                subscription_id=local_sub_id,
                stripe_invoice_id=stripe_id,
                stripe_customer_id=inv.get("customer"),
                stripe_subscription_id=stripe_sub_id,
                status=inv.get("status") or "open",
                currency=inv.get("currency"),
                subtotal=_maybe_num(inv.get("subtotal")),
                total=_maybe_num(inv.get("total")),
                hosted_invoice_url=inv.get("hosted_invoice_url"),
                period_start=period_start,
                period_end=period_end,
            )
            self.db.add(row)

        await self.db.flush()
        await self.db.refresh(row)

        if mirror_lines:
            await self.replace_lines_from_stripe_payload(invoice_row=row, inv=inv)

        return row

    async def replace_lines_from_stripe_payload(self, *, invoice_row: Invoice, inv: dict) -> None:
        """
        Replace invoice_lines for this invoice using Stripe invoice payload.
        Uses pure ORM deletes (no raw SQL).
        """
        # Delete existing lines
        existing = await self.db.execute(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_row.id)
        )
        for li in existing.scalars().all():
            await self.db.delete(li)

        # Insert new lines
        lines = (((inv or {}).get("lines") or {}).get("data") or [])
        for li in lines:
            line_type = li.get("type") or "unknown"
            qty = float(li.get("quantity") or 1.0)
            unit_price_cents = _compute_unit_price_cents(li)
            unit_price = (unit_price_cents / 100.0) if unit_price_cents is not None else 0.0
            amount = _cents_to_amount(li.get("amount")) or 0.0
            feature_key = _extract_feature_key(li)

            self.db.add(
                InvoiceLine(
                    invoice_id=invoice_row.id,
                    line_type=line_type,
                    feature_key=feature_key,
                    quantity=qty,
                    unit_price=unit_price,
                    amount=amount,
                )
            )

        await self.db.flush()

    async def list_for_account(
        self, *, project_id: str, account_id: str, limit: int = 50, offset: int = 0
    ) -> List[Invoice]:
        sub_q = select(Subscription.stripe_subscription_id).where(
            Subscription.project_id == project_id,
            Subscription.account_id == account_id,
        )
        q = (
            select(Invoice)
            .where(
                Invoice.project_id == project_id,
                Invoice.stripe_subscription_id.in_(sub_q),
            )
            .order_by(desc(Invoice.created_at))
            .offset(offset)
            .limit(limit)
        )
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def get_by_id(self, invoice_id: UUID) -> Optional[Invoice]:
        res = await self.db.execute(select(Invoice).where(Invoice.id == invoice_id))
        return res.scalar_one_or_none()

    async def get_by_stripe_id(self, project_id: str, stripe_invoice_id: str) -> Optional[Invoice]:
        res = await self.db.execute(
            select(Invoice).where(
                Invoice.project_id == project_id,
                Invoice.stripe_invoice_id == stripe_invoice_id,
            )
        )
        return res.scalar_one_or_none()

    async def get_detail_with_lines(self, invoice_id: UUID) -> tuple[Optional[Invoice], List[InvoiceLine]]:
        inv_res = await self.db.execute(select(Invoice).where(Invoice.id == invoice_id))
        invoice = inv_res.scalar_one_or_none()
        if not invoice:
            return None, []
        lines_res = await self.db.execute(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id).order_by(InvoiceLine.id)
        )
        return invoice, list(lines_res.scalars().all())
    
# -------------------- Usage Records (metered billing) --------------------
class UsageRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_event(
        self,
        *,
        project_id: str,
        account_id: str,
        metric_key: str,
        quantity: float,
        occurred_at: datetime,
        source_id: Optional[str],
        meta: Optional[dict],
    ) -> UsageRecord:
        """
        Insert a usage event; if source_id is provided and duplicates an existing row,
        return the existing row (idempotent).
        """
        if source_id:
            existing = await self.db.execute(
                select(UsageRecord).where(
                    UsageRecord.project_id == project_id,
                    UsageRecord.account_id == account_id,
                    UsageRecord.metric_key == metric_key,
                    UsageRecord.source_id == source_id,
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                return row

        row = UsageRecord(
            project_id=project_id,
            account_id=account_id,
            metric_key=metric_key,
            quantity=float(quantity),
            occurred_at=occurred_at,
            source_id=source_id,
            meta=meta or None,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def summarize_window(
        self,
        *,
        project_id: str,
        account_id: str,
        start: datetime,
        end: datetime,
    ) -> list[tuple[str, float]]:
        """
        Sum by metric_key in [start, end).
        """
        res = await self.db.execute(
            select(UsageRecord.metric_key, func.coalesce(func.sum(UsageRecord.quantity), 0.0))
            .where(
                UsageRecord.project_id == project_id,
                UsageRecord.account_id == account_id,
                UsageRecord.occurred_at >= start,
                UsageRecord.occurred_at < end,
            )
            .group_by(UsageRecord.metric_key)
            .order_by(UsageRecord.metric_key)
        )
        return [(mk, float(total)) for mk, total in res.all()]
    

class IdempotencyRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, *, project_id: str, key: str) -> Optional[IdempotencyKey]:
        res = await self.db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.project_id == project_id,
                IdempotencyKey.key == key,
            )
        )
        return res.scalar_one_or_none()

    async def create_in_progress(self, *, project_id: str, key: str, request_hash: str) -> IdempotencyKey:
        row = IdempotencyKey(
            project_id=project_id,
            key=key,
            request_hash=request_hash,
            status="in_progress",
            response=None,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def mark_succeeded(self, row: IdempotencyKey, response_payload: dict) -> IdempotencyKey:
        row.status = "succeeded"
        row.response = response_payload
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def mark_failed(self, row: IdempotencyKey, response_payload: Optional[dict] = None) -> IdempotencyKey:
        row.status = "failed"
        if response_payload is not None:
            row.response = response_payload
        await self.db.flush()
        await self.db.refresh(row)
        return row
    

class EntitlementsRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, *, project_id: str, account_id: str) -> Optional[EntitlementsCache]:
        res = await self.db.execute(
            select(EntitlementsCache).where(
                EntitlementsCache.project_id == project_id,
                EntitlementsCache.account_id == account_id,
            )
        )
        return res.scalar_one_or_none()

    async def upsert(
        self, *, project_id: str, account_id: str, as_of: datetime, payload: dict
    ) -> EntitlementsCache:
        row = await self.get(project_id=project_id, account_id=account_id)
        if row:
            row.as_of = as_of
            row.payload = payload
            await self.db.flush()
            await self.db.refresh(row)
            return row

        row = EntitlementsCache(
            project_id=project_id,
            account_id=account_id,
            as_of=as_of,
            payload=payload,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def invalidate(self, *, project_id: str, account_id: str) -> None:
        row = await self.get(project_id=project_id, account_id=account_id)
        if row:
            await self.db.delete(row)
            await self.db.flush()

# -------------------- Helpers --------------------

def _from_epoch(ts: Optional[int]) -> Optional[datetime]:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)

def _maybe_num(v):
    return float(v) if v is not None else None

def _cents_to_amount(v) -> Optional[float]:
    return (float(v) / 100.0) if v is not None else None

def _compute_unit_price_cents(item: dict) -> Optional[int]:
    price = item.get("price") or {}
    if isinstance(price.get("unit_amount"), int):
        return price["unit_amount"]
    amount = item.get("amount")
    qty = item.get("quantity") or 1
    if isinstance(amount, int) and qty:
        return int(round(amount / qty))
    return None

def _extract_feature_key(item: dict) -> Optional[str]:
    md = item.get("metadata") or {}
    if "feature_key" in md:
        return md["feature_key"]
    price = item.get("price") or {}
    if price.get("nickname"):
        return price["nickname"]
    plan = item.get("plan") or {}
    if plan.get("nickname"):
        return plan["nickname"]
    return None