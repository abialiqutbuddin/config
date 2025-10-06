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
from app.payments.stripe_provider import StripePaymentProvider

class EngineError(ValueError):
    pass

class SubscriptionEngine:
    def __init__(self, db: AsyncSession, stripe: StripePaymentProvider, project_id: str):
        self.db = db
        self.stripe = stripe
        self.project_id = project_id

    # ---------------- Core helpers ----------------
    async def _load_plan(self, plan_code: str) -> Tuple[UUID, dict]:
        cfg = await ConfigRepo(self.db).get_latest(self.project_id)
        if not cfg:
            raise EngineError("No config published for this project")
        plans = cfg.json.get("plans", {})
        plan = plans.get(plan_code)
        if not plan:
            raise EngineError(f"planCode '{plan_code}' not found")
        return cfg.id, plan

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

    def _config_mode(self, cfg_json: dict) -> str:
        # default to checkout if not specified
        return (cfg_json.get("payment") or {}).get("mode", "checkout")

    # ---------------- Public operations ----------------
    async def create_subscription(self, body: CreateSubscriptionRequest) -> SubscriptionResponse:
        """
        1) Validate plan and config
        2) Ensure Stripe customer
        3) Either create Checkout Session or direct Subscription
        4) Persist local row with pinned config_version_id
        """
        # Resolve config + plan
        cfg = await ConfigRepo(self.db).get_latest(self.project_id)
        if not cfg:
            raise EngineError("No config published for this project")
        config_mode = self._config_mode(cfg.json)

        config_version_id, plan = cfg.id, (cfg.json.get("plans") or {}).get(body.planCode)
        if not plan:
            raise EngineError(f"planCode '{body.planCode}' not found")

        price_id = self._price_id_from_plan(plan)
        trial_days = self._trial_days_from_plan(plan)
        bundle = build_bundle(plan)

        # Ensure Stripe customer
        customer_id = self.stripe.ensure_customer(
            account_id=body.accountId,
            project_id=self.project_id,
            email=None,
            metadata=(body.metadata or {}),
        )

        # Decide flow: request flag wins; else config default
        flow = "checkout" if body.checkout else ("checkout" if config_mode == "checkout" else "direct")

        # Create local row early as 'pending'
        repo = SubscriptionRepo(self.db)
        sub = await repo.create(
            project_id=self.project_id,
            account_id=body.accountId,
            plan_code=body.planCode,
            quantity=body.quantity,
            status="pending",
            config_version_id=config_version_id,
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
            # Hosted Checkout — subscription will be created after payment
            # You must handle `checkout.session.completed` in webhook to mark active.
            # Use generic URLs for now; you can pass project-specific ones via config later.
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

        else:
            # Direct API subscription — created immediately
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

            # Persist Stripe result
            sub = await repo.update(
                sub,
                stripe_subscription_id=stripe_sub_id,
                status=status,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
            )
            await self.db.commit()

        # Response DTO
        return SubscriptionResponse(
            id=sub.id,
            projectId=sub.project_id,
            accountId=sub.account_id,
            planCode=sub.plan_code,
            quantity=sub.quantity,
            status=status,
            configVersionId=config_version_id,
            stripeCustomerId=sub.stripe_customer_id,
            stripeSubscriptionId=stripe_sub_id or sub.stripe_subscription_id,
            currentPeriodStart=_iso(current_period_start or sub.current_period_start),
            currentPeriodEnd=_iso(current_period_end or sub.current_period_end),
            cancelAtPeriodEnd=sub.cancel_at_period_end,
            checkoutUrl=checkout_url,
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
        # Load plan & price
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

        # Persist
        repo = SubscriptionRepo(self.db)
        subscription = await repo.update(
            subscription,
            plan_code=new_plan_code,
            quantity=quantity,
            status=updated["status"],
            current_period_start=_from_epoch(updated.get("current_period_start")),
            current_period_end=_from_epoch(updated.get("current_period_end")),
            config_version_id=config_version_id,  # optional; pin to current config version
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
            checkoutUrl=None,  # filled when checkout flow is used during create
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