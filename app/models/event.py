import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("endpoints.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_body: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
"""
event.py — Event ORM model (immutable audit record).

Each incoming webhook is stored as an Event. This is an append-only log:
once written, it should never be modified.

Key fields:
- endpoint_id: which endpoint received this webhook
- idempotency_key: optional dedup key from the sender
- request_body: full raw payload stored as JSONB

Indexes:
- (endpoint_id, received_at) for efficient event listing per endpoint
- idempotency_key for dedup lookups
"""
