from sqlalchemy import Column, String, TIMESTAMP, JSON, Integer, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
import uuid

Base = declarative_base()


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String, nullable=False)
    version_label = Column(String, nullable=False)
    json = Column(JSON, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    project_id = Column(String, primary_key=True)
    key = Column(String, primary_key=True)
    request_hash = Column(String)
    response = Column(JSON)
    status = Column(String)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))