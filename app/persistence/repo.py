from __future__ import annotations
from typing import Optional, List
from uuid import UUID
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import (
    ConfigVersion,
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

    async def update(self, sub: Subscription, **fields) -> Subscription:
        for k, v in fields.items():
            setattr(sub, k, v)
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
        cancel_at_period_end: Optional[bool] = None,
        stripe_subscription_id: Optional[str] = None,
    ) -> Subscription:
        if status is not None:
            sub.status = status
        if current_period_start is not None:
            sub.current_period_start = current_period_start
        if current_period_end is not None:
            sub.current_period_end = current_period_end
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

    async def record_if_new(self, *, event_id: str, project_id: Optional[str], event_type: str, payload: dict) -> bool:
        """
        Returns True if the event was newly recorded, False if it already existed (dedup).
        """
        existing = await self.db.get(PaymentEvent, event_id)
        if existing:
            return False
        self.db.add(PaymentEvent(id=event_id, project_id=project_id, type=event_type, payload=payload))
        await self.db.flush()
        return True


# -------------------- Invoices (mirror from Stripe) --------------------

class InvoiceRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_from_stripe(self, *, project_id: str, inv: dict) -> Invoice:
        """
        Insert or update a mirrored invoice row from a Stripe invoice object.
        """
        stripe_id = inv["id"]
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
        else:
            row = Invoice(
                project_id=project_id,
                stripe_invoice_id=stripe_id,
                stripe_customer_id=inv.get("customer"),
                stripe_subscription_id=inv.get("subscription"),
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
        return row
    
    async def list_for_account(
        self, *, project_id: str, account_id: str, limit: int = 50, offset: int = 0
    ) -> List[Invoice]:
        q = (
            select(Invoice)
            .where(
                Invoice.project_id == project_id,
                # join via subscription id on invoices we've mirrored
                # if you don’t store account_id on invoice, we infer by subscription match
            )
            .order_by(desc(Invoice.created_at))
            .offset(offset)
            .limit(limit)
        )
        # If you want to filter by account, map via subscriptions:
        # SELECT invoices WHERE invoice.stripe_subscription_id IN (SELECT ... FROM subscriptions WHERE account_id=...)
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


# -------------------- Helpers --------------------

def _from_epoch(ts: Optional[int]) -> Optional[datetime]:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)

def _maybe_num(v):
    return float(v) if v is not None else None