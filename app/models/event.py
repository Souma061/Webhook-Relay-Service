import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("endpoints.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_body: Mapped[dict] = mapped_column(JSONB)
    # Stores lowercased HTTP request headers so the filter engine can
    # evaluate expressions like: headers."x-github-event" == 'push'
    request_headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending", index=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    # DLQ soft-delete: operator can discard a failed event to hide it from the queue
    is_discarded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
"""
event.py — Event ORM model (immutable audit record).

Each incoming webhook is stored as an Event. This is an append-only log;
the payload itself is never modified, but operator metadata (is_discarded)
can be updated.

Key fields:
- endpoint_id: which endpoint received this webhook
- idempotency_key: optional dedup key from the sender
- request_body: full raw payload stored as JSONB
- is_discarded: soft-delete flag set by operator via POST /api/dlq/{id}/discard
- discarded_at: timestamp of when the event was discarded

Indexes:
- (endpoint_id, received_at) for efficient event listing per endpoint
"""
