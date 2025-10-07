# app/engine/engine.py
from __future__ import annotations
from typing import Optional, Tuple
from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.strategies.registry import build_bundle
from app.persistence.repo import ConfigRepo, SubscriptionRepo
from app.persistence.models import Subscription
from app.schemas.api_models import CreateSubscriptionRequest, SubscriptionResponse
from app.payments.types import PaymentProvider  # interface for stripe/fake

class EngineError(ValueError):
    pass


class SubscriptionEngine:
    def __init__(self, db: AsyncSession, stripe: PaymentProvider, project_id: str):
        self.db = db
        self.stripe = stripe
        self.project_id = project_id

    # ---------------- Core helpers ----------------

    async def _load_plan(self, plan_code: str) -> Tuple[UUID, dict]:
        """
        Load latest config for this project and return (config_version_id, plan_dict)
        where plan is found by plans[i].code == plan_code.
        """
        cfg = await ConfigRepo(self.db).get_latest(self.project_id)
        if not cfg:
            raise EngineError("No config published for this project")

        plans = (cfg.json or {}).get("plans", [])
        if not isinstance(plans, list):
            raise EngineError("Config is invalid: 'plans' must be an array")

        for p in plans:
            if isinstance(p, dict) and p.get("code") == plan_code:
                return cfg.id, p

        raise EngineError(f"planCode '{plan_code}' not found")

    def _price_id_from_plan(self, plan: dict) -> str:
        billing = plan.get("billing") or {}
        price_id = billing.get("priceId")
        if not price_id:
            raise EngineError("Plan is missing billing.priceId")
        return price_id

    def _trial_days_from_plan(self, plan: dict) -> int:
        td = plan.get("trialDays")
        if td is None:
            return 0
        if not isinstance(td, int) or td < 0:
            raise EngineError("trialDays must be a non-negative integer")
        return td

    def _decide_flow(self, request_checkout: Optional[bool]) -> str:
        """
        Schema no longer has payment.mode.
        - If request explicitly asks for checkout True/False, honor it.
        - Otherwise default to 'checkout' (safe default for real payments).
        """
        if request_checkout is True:
            return "checkout"
        if request_checkout is False:
            return "direct"
        return "checkout"

    # ---------------- Public operations ----------------

    async def create_subscription(self, body: CreateSubscriptionRequest) -> SubscriptionResponse:
        """
        1) Validate plan and config
        2) Ensure customer
        3) Create hosted Checkout OR direct subscription
        4) Persist local row
        """
        if body.quantity is None or body.quantity < 1:
            raise EngineError("quantity must be >= 1")

        # Resolve plan from latest config (array by code)
        cfg = await ConfigRepo(self.db).get_latest(self.project_id)
        if not cfg:
            raise EngineError("No config published for this project")

        _, plan = await self._load_plan(body.planCode)
        price_id = self._price_id_from_plan(plan)
        trial_days = self._trial_days_from_plan(plan)
        bundle = build_bundle(plan)

        # Ensure customer
        customer_id = self.stripe.ensure_customer(
            account_id=body.accountId,
            project_id=self.project_id,
            email=None,
            metadata=(body.metadata or {}),
        )

        # Decide flow (schema has no payment.mode anymore)
        flow = self._decide_flow(body.checkout)

        repo = SubscriptionRepo(self.db)
        sub = await repo.create(
            project_id=self.project_id,
            account_id=body.accountId,
            plan_code=body.planCode,
            quantity=body.quantity,
            status="pending",
            config_version_id=cfg.id,
            stripe_customer_id=customer_id,
            stripe_subscription_id=None,
            current_period_start=None,
            current_period_end=None,
            cancel_at_period_end=False,
            meta=(body.metadata or {}),
        )
        await self.db.commit()

        checkout_url: Optional[str] = None
        stripe_sub_id: Optional[str] = None
        current_period_start: Optional[datetime] = None
        current_period_end: Optional[datetime] = None
        status: str = "pending"

        if flow == "checkout":
            # Hosted Checkout — Stripe will create the subscription after payment.
            # Webhook should promote local sub from pending -> active.
            success_url = "https://example.com/success?session_id={CHECKOUT_SESSION_ID}"
            cancel_url = "https://example.com/cancel"
            checkout_url, _session_id = self.stripe.create_checkout_session(
                customer_id=customer_id,
                price_id=price_id,
                quantity=body.quantity,
                success_url=success_url,
                cancel_url=cancel_url,
                trial_days=trial_days,
                coupon=body.coupon,
                metadata={
                    "project_id": self.project_id,
                    "account_id": body.accountId,
                    "subscription_local_id": str(sub.id),
                },
            )
            status = "pending"

            # (Optional) persist checkout_url if you add a column:
            # sub = await repo.update(sub, checkout_url=checkout_url)
            # await self.db.commit()

        else:
            # Direct subscription — created immediately
            created = self.stripe.create_subscription(
                customer_id=customer_id,
                price_id=price_id,
                quantity=body.quantity,
                trial_days=trial_days,
                coupon=body.coupon,
                collection_method=bundle.invoicing.collection_method(),
                proration_behavior=bundle.proration.proration_behavior(),
                metadata={
                    "project_id": self.project_id,
                    "account_id": body.accountId,
                    "subscription_local_id": str(sub.id),
                },
            )
            stripe_sub_id = created["id"]
            status = created["status"]
            current_period_start = _from_epoch(created.get("current_period_start"))
            current_period_end = _from_epoch(created.get("current_period_end"))

            sub = await repo.update(
                sub,
                stripe_subscription_id=stripe_sub_id,
                status=status,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
            )
            await self.db.commit()

        # DTO
        return SubscriptionResponse(
            id=sub.id,
            projectId=sub.project_id,
            accountId=sub.account_id,
            planCode=sub.plan_code,
            quantity=sub.quantity,
            status=status,
            configVersionId=cfg.id,
            stripeCustomerId=sub.stripe_customer_id,
            stripeSubscriptionId=stripe_sub_id or sub.stripe_subscription_id,
            currentPeriodStart=_iso(current_period_start or sub.current_period_start),
            currentPeriodEnd=_iso(current_period_end or sub.current_period_end),
            cancelAtPeriodEnd=sub.cancel_at_period_end,
            checkoutUrl=checkout_url,   # returned even if not persisted
            metadata=sub.meta or {},
        )

    async def change_plan(
        self,
        *,
        subscription: Subscription,
        new_plan_code: str,
        quantity: int,
        proration_behavior: str = "create_prorations",
    ) -> SubscriptionResponse:
        if quantity < 1:
            raise EngineError("quantity must be >= 1")

        config_version_id, plan = await self._load_plan(new_plan_code)
        price_id = self._price_id_from_plan(plan)
        bundle = build_bundle(plan)

        if not subscription.stripe_subscription_id:
            raise EngineError("Stripe subscription not set; cannot change plan")

        updated = self.stripe.update_subscription(
            subscription_id=subscription.stripe_subscription_id,
            price_id=price_id,
            quantity=quantity,
            proration_behavior=proration_behavior or bundle.proration.proration_behavior(),
        )

        repo = SubscriptionRepo(self.db)
        subscription = await repo.update(
            subscription,
            plan_code=new_plan_code,
            quantity=quantity,
            status=updated["status"],
            current_period_start=_from_epoch(updated.get("current_period_start")),
            current_period_end=_from_epoch(updated.get("current_period_end")),
            config_version_id=config_version_id,
        )
        await self.db.commit()
        return self._dto(subscription)

    async def cancel(self, *, subscription: Subscription, at_period_end: bool) -> SubscriptionResponse:
        if not subscription.stripe_subscription_id:
            raise EngineError("Stripe subscription not set; cannot cancel")

        res = self.stripe.cancel_subscription(
            subscription_id=subscription.stripe_subscription_id,
            at_period_end=at_period_end,
        )
        repo = SubscriptionRepo(self.db)
        subscription = await repo.update(
            subscription,
            status=res["status"],
            cancel_at_period_end=res["cancel_at_period_end"],
        )
        await self.db.commit()
        return self._dto(subscription)

    async def resume(self, *, subscription: Subscription) -> SubscriptionResponse:
        if not subscription.stripe_subscription_id:
            raise EngineError("Stripe subscription not set; cannot resume")

        res = self.stripe.resume_subscription(subscription_id=subscription.stripe_subscription_id)
        repo = SubscriptionRepo(self.db)
        subscription = await repo.update(
            subscription,
            status=res["status"],
            cancel_at_period_end=res["cancel_at_period_end"],
        )
        await self.db.commit()
        return self._dto(subscription)

    # ---------------- Formatting helpers ----------------

    def _dto(self, sub: Subscription) -> SubscriptionResponse:
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
            currentPeriodStart=_iso(sub.current_period_start),
            currentPeriodEnd=_iso(sub.current_period_end),
            cancelAtPeriodEnd=sub.cancel_at_period_end,
            checkoutUrl=getattr(sub, "checkout_url", None),
            metadata=sub.meta or {},
        )


def _from_epoch(ts: Optional[int]) -> Optional[datetime]:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)

def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()