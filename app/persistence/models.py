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
    Text,
    UniqueConstraint,
    Boolean,
    text as sqltext,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from .base import Base



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

    project_id = Column(String, primary_key=True)
    key = Column(String, primary_key=True)

    request_hash = Column(String, nullable=False)
    response = Column(JSON, nullable=True)     # store serialized response payload for quick replay
    status = Column(String, nullable=False)    # e.g., 'in_progress' | 'succeeded' | 'failed'

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
    quantity = Column(Integer, nullable=False, default=1)  # keep 'quantity', not 'seats'
    status = Column(String, nullable=False, index=True, default="pending")

    config_version_id = Column(UUID(as_uuid=True), ForeignKey("config_versions.id"), nullable=False, index=True)
    config_version = relationship("ConfigVersion")

    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_subscription_id = Column(String, nullable=True, unique=True)

    # NOW NULLABLE to allow 'pending' rows
    current_period_start = Column(TIMESTAMP(timezone=True), nullable=True)
    current_period_end = Column(TIMESTAMP(timezone=True), nullable=True)

    # NEW optional field
    trial_end_at = Column(TIMESTAMP(timezone=True), nullable=True)

    cancel_at_period_end = Column(Boolean, nullable=False, default=False)

    # request/extra metadata
    meta = Column(JSON, nullable=True)

    # money snapshot
    currency = Column(String(3), nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)

    # optional checkout URL
    checkout_url = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), server_default=sqltext("timezone('utc', now())"), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)    
    __table_args__ = (
        Index("ix_subs_project_account", "project_id", "account_id"),
        Index("ix_subs_project_status", "project_id", "status"),
    )


# -------------------------
# Payment Events (webhook dedupe/audit)
# -------------------------
class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String, nullable=False, index=True)
    provider = Column(String, nullable=False, index=True)     # e.g. 'stripe'
    event_type = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False)                    # raw provider payload
    received_at = Column(
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

    # NEW: local subscription FK (nullable, backfilled when we can resolve)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True, index=True)

    stripe_invoice_id = Column(String, nullable=False, unique=True)
    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_subscription_id = Column(String, nullable=True, index=True)

    status = Column(String, nullable=False)
    currency = Column(String, nullable=True)
    subtotal = Column(Numeric(12, 2, asdecimal=True), nullable=True)
    total = Column(Numeric(12, 2, asdecimal=True), nullable=True)
    hosted_invoice_url = Column(Text, nullable=True)

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=sqltext("timezone('utc', now())"),
        nullable=False,
    )
    period_start = Column(TIMESTAMP(timezone=True), nullable=True)
    period_end = Column(TIMESTAMP(timezone=True), nullable=True)

# -------------------------
# Invoice Lines
# -------------------------
class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)

    line_type = Column(String, nullable=False)      # e.g. 'subscription', 'invoiceitem'
    feature_key = Column(String, nullable=True)
    quantity = Column(Numeric(18, 6, asdecimal=True), nullable=False)
    unit_price = Column(Numeric(12, 4, asdecimal=True), nullable=False)
    amount = Column(Numeric(12, 2, asdecimal=True), nullable=False)

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

# -------------------------
# Entitlements Cache
# -------------------------
class EntitlementsCache(Base):
    __tablename__ = "entitlements_cache"

    project_id = Column(String, primary_key=True)
    account_id = Column(String, primary_key=True)
    as_of = Column(TIMESTAMP(timezone=True), nullable=False)
    payload = Column(JSON, nullable=False)