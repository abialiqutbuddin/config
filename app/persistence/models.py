from __future__ import annotations

import uuid
from sqlalchemy import (
    Column,
    Index,
    String,
    TIMESTAMP,
    JSON,
    Integer,
    ForeignKey,
    Numeric,
    UniqueConstraint,
    Boolean,
    text as sqltext,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


# -------------------------
# Config Versions
# -------------------------
class ConfigVersion(Base):
    __tablename__ = "config_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String, nullable=False, index=True)
    version_label = Column(String, nullable=False)
    json = Column(JSON, nullable=False)

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=sqltext("timezone('utc', now())"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "version_label", name="uq_config_versions_project_label"
        ),
    )


# -------------------------
# Idempotency Keys
# -------------------------
class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    # composite primary key for O(1) lookups
    project_id = Column(String, primary_key=True)
    key = Column(String, primary_key=True)

    request_hash = Column(String)
    response = Column(JSON)
    status = Column(String)

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=sqltext("timezone('utc', now())"),
        nullable=False,
    )


# -------------------------
# Subscriptions
# -------------------------
class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String, nullable=False, index=True)
    account_id = Column(String, nullable=False, index=True)

    plan_code = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, server_default="1")

    # pending | trialing | active | past_due | canceled
    status = Column(String, nullable=False, server_default="pending")

    config_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("config_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True, unique=True, index=True)

    current_period_start = Column(TIMESTAMP(timezone=True), nullable=True)
    current_period_end = Column(TIMESTAMP(timezone=True), nullable=True)
    cancel_at_period_end = Column(Boolean, nullable=False, server_default="false")

    # optional: keep checkout URL for troubleshooting
    checkout_url = Column(String, nullable=True)

    meta = Column("metadata", JSON, nullable=True)

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=sqltext("timezone('utc', now())"),
        nullable=False,
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_subs_project_account", "project_id", "account_id"),
        Index("ix_subs_project_status", "project_id", "status"),
    )


# -------------------------
# Payment Events (webhook dedupe/audit)
# -------------------------
class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id = Column(String, primary_key=True)  # Stripe event id (or fake id in dev)
    project_id = Column(String, nullable=True, index=True)
    type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=sqltext("timezone('utc', now())"),
        nullable=False,
    )


# -------------------------
# Invoices (mirrored)
# -------------------------
class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String, nullable=False, index=True)

    stripe_invoice_id = Column(String, nullable=False, unique=True)
    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_subscription_id = Column(String, nullable=True, index=True)

    # draft | open | paid | uncollectible | void
    status = Column(String, nullable=False)

    currency = Column(String, nullable=True)

    # if you want exact money math in DB, keep as decimal=True and parse in DTOs
    subtotal = Column(Numeric(precision=12, scale=2, asdecimal=True), nullable=True)
    total = Column(Numeric(precision=12, scale=2, asdecimal=True), nullable=True)

    hosted_invoice_url = Column(String, nullable=True)

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=sqltext("timezone('utc', now())"),
        nullable=False,
    )
    period_start = Column(TIMESTAMP(timezone=True), nullable=True)
    period_end = Column(TIMESTAMP(timezone=True), nullable=True)


# -------------------------
# Usage Records (metered events)
# -------------------------
class UsageRecord(Base):
    __tablename__ = "usage_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    project_id = Column(String, nullable=False, index=True)
    account_id = Column(String, nullable=False, index=True)
    metric_key = Column(String, nullable=False, index=True)

    # Numeric keeps precision; use float in DTOs if you prefer
    quantity = Column(Numeric(precision=16, scale=6, asdecimal=True), nullable=False)

    # optional external id for idempotency (e.g. from upstream system)
    source_id = Column(String, nullable=True)

    occurred_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sqltext("timezone('utc', now())"),
    )

    meta = Column("metadata", JSON, nullable=True)

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=sqltext("timezone('utc', now())"),
        nullable=False,
    )

    __table_args__ = (
        # If source_id is provided, enforce dedupe per (project, account, metric, source).
        UniqueConstraint(
            "project_id", "account_id", "metric_key", "source_id", name="uq_usage_dedupe"
        ),
        Index("ix_usage_window", "project_id", "account_id", "metric_key", "occurred_at"),
    )