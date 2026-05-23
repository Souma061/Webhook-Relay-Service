import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), index=True)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("routes.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    request_url: Mapped[str] = mapped_column(Text)
    request_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
"""
delivery_attempt.py — DeliveryAttempt ORM model (audit log).

Records every HTTP call made by the relay. One event can have multiple
delivery attempts (one per retry) across multiple routes.

Key fields:
- attempt_number: 0-indexed retry counter
- request_url / request_body: what was sent
- response_status / response_body: what came back
- error: exception message if the request failed entirely
- duration_ms: how long the HTTP call took

This table powers the audit trail, stats dashboard, and failure analysis.
"""
