from __future__ import annotations
from typing import Optional, Tuple, Dict, Any, List
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


class SubscriptionEngine:
    """
    Engine aligned to the schema:

    {
      "projectId": "...",
      "currency": "USD",
      "plans": [
        { "code": "BASIC", "cadence": "monthly" | "annual", "price": 9.99,
          "trial": { "days": 14 } | null,
          "features": [...],
          "strategies": {
            "ProrationStrategy": "...",          # required
            "InvoicingStrategy": "...",          # required
            "EntitlementStrategy": "...",        # required
            "MeteringStrategy": "...",           # optional
            "SeatStrategy": "..."                # optional
          }
        }
      ],
      "billingAnchor": { "type": "anniversary" | "calendar" }?,
      "timeZone": "..."?
    }

    Notes:
    - We DO NOT expect a Stripe priceId in config.
    - We resolve Stripe price via provider.resolve_price_id(currency, cadence, price).
    """

    def __init__(self, db: AsyncSession, stripe: PaymentProvider, project_id: str):
        self.db = db
        self.stripe = stripe
        self.project_id = project_id

    # ---------------- helpers for config/schema ----------------

    async def _load_config(self) -> dict:
        cfg = await ConfigRepo(self.db).get_latest(self.project_id)
        if not cfg:
            raise EngineError("No config published for this project")
        if not isinstance(cfg.json, dict):
            raise EngineError("Config JSON must be an object")
        return cfg.json

    @staticmethod
    def _extract_currency(cfg_json: dict) -> str:
        cur = (cfg_json or {}).get("currency")
        if not isinstance(cur, str) or len(cur) != 3:
            raise EngineError("Config is invalid: 'currency' must be a 3-letter code")
        return cur.upper()

    @staticmethod
    def _plans_array(cfg_json: dict) -> List[dict]:
        plans = (cfg_json or {}).get("plans")
        if not isinstance(plans, list) or not plans:
            raise EngineError("Config is invalid: 'plans' must be a non-empty array")
        return plans

    @staticmethod
    def _pick_cadence_hint(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        c = (metadata or {}).get("cadence")
        if isinstance(c, str) and c in ("monthly", "annual"):
            return c
        return None

    def _find_plan(self, plans: List[dict], *, plan_code: str, cadence_hint: Optional[str]) -> dict:
        """
        Find a plan by code (and cadence if provided). If multiple with same code:
        - prefer cadence_hint if present
        - else prefer 'monthly'
        - else first match
        """
        matches = [p for p in plans if isinstance(p, dict) and p.get("code") == plan_code]
        if not matches:
            raise EngineError(f"planCode '{plan_code}' not found")

        if len(matches) == 1:
            return matches[0]

        if cadence_hint:
            for p in matches:
                if p.get("cadence") == cadence_hint:
                    return p
        for p in matches:
            if p.get("cadence") == "monthly":
                return p
        return matches[0]

    @staticmethod
    def _extract_cadence(plan: dict) -> str:
        cad = plan.get("cadence")
        if cad not in ("monthly", "annual"):
            raise EngineError("Plan is missing a valid 'cadence' (monthly|annual)")
        return cad

    @staticmethod
    def _extract_price(plan: dict) -> float:
        price = plan.get("price")
        try:
            price = float(price)
        except Exception:
            raise EngineError("Plan 'price' must be a number")
        if price < 0:
            raise EngineError("Plan 'price' cannot be negative")
        return price

    @staticmethod
    def _trial_days(plan: dict) -> int:
        t = plan.get("trial")
        if t is None:
            return 0
        if not isinstance(t, dict):
            raise EngineError("Plan 'trial' must be an object or null")
        days = t.get("days", 0)
        if not isinstance(days, int) or days < 0:
            raise EngineError("trial.days must be a non-negative integer")
        return days

    @staticmethod
    def _strategies(plan: dict) -> dict:
        s = plan.get("strategies")
        if not isinstance(s, dict):
            raise EngineError("Plan 'strategies' must be an object")
        return s

    @staticmethod
    def _decide_flow(request_checkout: Optional[bool]) -> str:
        """
        Schema has no payment.mode now.
        - checkout=True  -> checkout
        - checkout=False -> direct
        - None           -> checkout (safe default)
        """
        if request_checkout is True:
            return "checkout"
        if request_checkout is False:
            return "direct"
        return "checkout"

    # ---------------- public operations ----------------

    async def create_subscription(self, body: CreateSubscriptionRequest, idempotency_key: Optional[str] = None) -> SubscriptionResponse:
        """
        1) Validate & select plan from array (by code [+ cadence hint if present])
        2) Resolve Stripe price_id from (currency, cadence, price)
        3) Ensure customer
        4) Create hosted Checkout OR direct subscription
        5) Persist local row
        """
        if body.quantity is None or body.quantity < 1:
            raise EngineError("quantity must be >= 1")

        cfg_json = await self._load_config()
        currency = self._extract_currency(cfg_json)
        plans = self._plans_array(cfg_json)

        cadence_hint = self._pick_cadence_hint(body.metadata)
        plan = self._find_plan(plans, plan_code=body.planCode, cadence_hint=cadence_hint)

        cadence = self._extract_cadence(plan)
        unit_price = self._extract_price(plan)
        trial_days = self._trial_days(plan)

        # --- NEW: enforce required strategy keys from schema ---
        s = self._strategies(plan)
        for req in ("ProrationStrategy", "InvoicingStrategy", "EntitlementStrategy"):
            if req not in s or not isinstance(s[req], str) or not s[req]:
                raise EngineError(f"Plan 'strategies.{req}' is required and must be a string")

        # build strategies bundle (your strategy classes can still read plan details)
        bundle = build_bundle(plan)

        # resolve Stripe price_id based on (currency, cadence, unit_price)
        price_id = self.stripe.resolve_price_id(
            currency=currency, cadence=cadence, unit_price=unit_price
        )

        # Ensure customer
        customer_id = self.stripe.ensure_customer(
            account_id=body.accountId,
            project_id=self.project_id,
            email=None,
            metadata=(body.metadata or {}),
        )

        flow = self._decide_flow(body.checkout)

        # Persist minimal local row first (pending)
        repo = SubscriptionRepo(self.db)
        sub = await repo.create(
            project_id=self.project_id,
            account_id=body.accountId,
            plan_code=body.planCode,
            quantity=body.quantity,
            status="pending",
            config_version_id=(await ConfigRepo(self.db).get_latest(self.project_id)).id,
            stripe_customer_id=customer_id,
            stripe_subscription_id=None,
            current_period_start=None,
            current_period_end=None,
            cancel_at_period_end=False,
            meta=(body.metadata or {}),
            currency=currency,
            unit_price=unit_price,
        )
        await self.db.commit()

        checkout_url: Optional[str] = None
        stripe_sub_id: Optional[str] = None
        current_period_start: Optional[datetime] = None
        current_period_end: Optional[datetime] = None
        status: str = "pending"

        if flow == "checkout":
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
                idempotency_key=idempotency_key,
                metadata={
                    "project_id": self.project_id,
                    "account_id": body.accountId,
                    "subscription_local_id": str(sub.id),
                    "plan_code": body.planCode,
                    "cadence": cadence,
                    "currency": currency,
                    "unit_price": unit_price,
                },
            )
            status = "pending"

            # Optional persistence of checkout_url if your model has the column.
            try:
                sub = await repo.update(sub, checkout_url=checkout_url)
                await self.db.commit()
            except TypeError:
                # repo.update does not accept checkout_url → ignore silently
                pass

        else:
            created = self.stripe.create_subscription(
                customer_id=customer_id,
                price_id=price_id,
                quantity=body.quantity,
                trial_days=trial_days,
                coupon=body.coupon,
                collection_method=bundle.invoicing.collection_method(),
                proration_behavior=bundle.proration.proration_behavior(),
                idempotency_key=idempotency_key,
                metadata={
                    "project_id": self.project_id,
                    "account_id": body.accountId,
                    "subscription_local_id": str(sub.id),
                    "plan_code": body.planCode,
                    "cadence": cadence,
                    "currency": currency,
                    "unit_price": unit_price,
                },
            )
            stripe_sub_id = created["id"]
            status = created["status"]
            current_period_start = _from_epoch(created.get("current_period_start"))
            current_period_end = _from_epoch(created.get("current_period_end"))
            trial_end_at = _from_epoch(created.get("trial_end"))

            sub = await repo.update(
                sub,
                stripe_subscription_id=stripe_sub_id,
                status=status,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                trial_end_at=trial_end_at,
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
            configVersionId=sub.config_version_id,
            stripeCustomerId=sub.stripe_customer_id,
            stripeSubscriptionId=stripe_sub_id or sub.stripe_subscription_id,
            currentPeriodStart=_iso(current_period_start or sub.current_period_start),
            currentPeriodEnd=_iso(current_period_end or sub.current_period_end),
            trialEndAt=_iso(trial_end_at or sub.trial_end_at),
            cancelAtPeriodEnd=sub.cancel_at_period_end,
            checkoutUrl=checkout_url if flow == "checkout" else getattr(sub, "checkout_url", None),
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

        cfg_json = await self._load_config()
        currency = self._extract_currency(cfg_json)
        plans = self._plans_array(cfg_json)

        cadence_hint = None
        if isinstance(subscription.meta, dict):
            h = subscription.meta.get("cadence")
            if isinstance(h, str) and h in ("monthly", "annual"):
                cadence_hint = h

        plan = self._find_plan(plans, plan_code=new_plan_code, cadence_hint=cadence_hint)
        cadence = self._extract_cadence(plan)
        unit_price = self._extract_price(plan)

        # validate strategies here too (defensive)
        s = self._strategies(plan)
        for req in ("ProrationStrategy", "InvoicingStrategy", "EntitlementStrategy"):
            if req not in s or not isinstance(s[req], str) or not s[req]:
                raise EngineError(f"Plan 'strategies.{req}' is required and must be a string")

        price_id = self.stripe.resolve_price_id(currency=currency, cadence=cadence, unit_price=unit_price)
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
            currency=currency,
            unit_price=unit_price,
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

    # ---------------- DTO ----------------

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
            trialEndAt=_iso(sub.trial_end_at),                   # <-- NEW
            cancelAtPeriodEnd=sub.cancel_at_period_end,
            checkoutUrl=getattr(sub, "checkout_url", None),
            metadata=sub.meta or {},
        )