from sqlalchemy import Column, Index, String, TIMESTAMP, JSON, Integer, ForeignKey, Numeric, UniqueConstraint, text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
import uuid


Base = declarative_base()


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String, nullable=False, index=True)
    version_label = Column(String, nullable=False)
    json = Column(JSON, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))
    __table_args__ = (
        UniqueConstraint("project_id", "version_label", name="uq_config_versions_project_label"),
    )

class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    project_id = Column(String, primary_key=True)
    key = Column(String, primary_key=True)
    request_hash = Column(String)
    response = Column(JSON)
    status = Column(String)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String, nullable=False, index=True)
    account_id = Column(String, nullable=False, index=True)
    plan_code = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, server_default="1")
    status = Column(String, nullable=False, server_default="pending")  # pending|trialing|active|past_due|canceled
    config_version_id = Column(UUID(as_uuid=True), ForeignKey("config_versions.id"), nullable=False, index=True)

    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True, unique=True)

    current_period_start = Column(TIMESTAMP(timezone=True), nullable=True)
    current_period_end = Column(TIMESTAMP(timezone=True), nullable=True)
    cancel_at_period_end = Column(Boolean, nullable=False, server_default="false")

    meta = Column("metadata", JSON, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))

# Store raw Stripe events for idempotency/audit
class PaymentEvent(Base):
    __tablename__ = "payment_events"
    id = Column(String, primary_key=True)  # Stripe event id
    project_id = Column(String, nullable=True, index=True)
    type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))

# Mirror invoices (minimal)
class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String, nullable=False, index=True)
    stripe_invoice_id = Column(String, nullable=False, unique=True)
    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_subscription_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False)  # draft|open|paid|uncollectible|void
    currency = Column(String, nullable=True)
    subtotal = Column(Numeric(asdecimal=False), nullable=True)
    total = Column(Numeric(asdecimal=False), nullable=True)
    hosted_invoice_url = Column(String, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))
    period_start = Column(TIMESTAMP(timezone=True), nullable=True)
    period_end = Column(TIMESTAMP(timezone=True), nullable=True)

# --- append this model ---
class UsageRecord(Base):
    __tablename__ = "usage_records"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    project_id = Column(String, nullable=False, index=True)
    account_id = Column(String, nullable=False, index=True)
    metric_key = Column(String, nullable=False, index=True)

    # quantity can be float or int depending on metric; Numeric keeps precision
    quantity = Column(Numeric(asdecimal=False), nullable=False)

    # optional external id for idempotency (e.g., event id from your system)
    source_id = Column(String, nullable=True)

    occurred_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    meta = Column("metadata", JSON, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))

    __table_args__ = (
        # if source_id is provided, enforce dedupe per (project, account, metric, source)
        UniqueConstraint("project_id", "account_id", "metric_key", "source_id", name="uq_usage_dedupe"),
        Index("ix_usage_window", "project_id", "account_id", "metric_key", "occurred_at"),
    )