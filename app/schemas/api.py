from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Any


class EndpointCreate(BaseModel):
    name: str
    hmac_secret: str | None = None


class EndpointUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class EndpointOut(BaseModel):
    id: UUID
    name: str
    is_active: bool
    created_at: datetime
    hmac_secret: str

    class Config:
        from_attributes = True


class RouteUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    transform_pipeline: list[dict] | None = None
    timeout_ms: int | None = None
    max_retries: int | None = None
    retry_backoff_ms: int | None = None
    is_active: bool | None = None


class SecretRotateOut(BaseModel):
    hmac_secret: str
    message: str = "Secret rotated. Update your webhook source immediately."


class RouteCreate(BaseModel):
    name: str
    url: str
    method: str = "POST"
    headers: dict[str, str] | None = None
    transform_pipeline: list[dict] | None = None
    timeout_ms: int = 10000
    max_retries: int = 5
    retry_backoff_ms: int = 1000


class RouteOut(BaseModel):
    id: UUID
    name: str
    url: str
    method: str
    is_active: bool
    timeout_ms: int
    max_retries: int
    created_at: datetime

    class Config:
        from_attributes = True


class EventOut(BaseModel):
    id: UUID
    endpoint_id: UUID
    idempotency_key: str | None
    request_body: Any
    received_at: datetime

    class Config:
        from_attributes = True


class DeliveryAttemptOut(BaseModel):
    id: UUID
    event_id: UUID
    route_id: UUID
    attempt_number: int
    request_url: str
    response_status: int | None
    error: str | None
    duration_ms: int | None
    attempted_at: datetime

    class Config:
        from_attributes = True


class WebhookResponse(BaseModel):
    status: str
    event_id: UUID
"""
api.py — Pydantic schemas for API request/response validation.

Defines the data shapes for creating endpoints/routes and viewing
events/delivery attempts. Used by FastAPI for auto-docs and validation.

Key schemas:
- EndpointCreate/Update/Out: create/update/list endpoints
- RouteCreate/Update/Out: add/update routes to an endpoint
- SecretRotateOut: returned once after secret rotation
- EventOut: view stored webhook events
- DeliveryAttemptOut: view delivery history
- WebhookResponse: what the sender gets back (202 accepted)
"""
