import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, func, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("endpoints.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(10), default="POST")
    headers: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    transform_pipeline: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=10000)
    max_retries: Mapped[int] = mapped_column(Integer, default=5)
    retry_backoff_ms: Mapped[int] = mapped_column(Integer, default=1000)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    endpoint = relationship("Endpoint", back_populates="routes")
"""
route.py — Route ORM model.

A route defines one delivery target for an endpoint. Each incoming
webhook fans out to all active routes on the endpoint.

Key fields:
- url: where to send the HTTP request
- transform_pipeline: list of transform steps (e.g., [{"type": "template", ...}])
- max_retries / retry_backoff_ms: retry policy for this destination
- timeout_ms: HTTP request timeout per destination

Relationships:
- Many Routes → One Endpoint
"""
