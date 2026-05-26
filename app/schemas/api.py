from pydantic import BaseModel, field_validator, ConfigDict
from uuid import UUID
from datetime import datetime
from typing import Any
import jmespath
from jmespath.exceptions import JMESPathError


class EndpointCreate(BaseModel):
    name: str
    hmac_secret: str | None = None


class EndpointUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class EndpointOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SecretRotateOut(BaseModel):
    hmac_secret: str
    message: str = "Secret rotated. Update your webhook source immediately."


def _validate_jmespath(v: str | None) -> str | None:
    if v is not None and v.strip() != "":
        try:
            jmespath.compile(v)
        except JMESPathError as e:
            raise ValueError(f"Invalid JMESPath expression: {e}")
    return v


class RouteCreate(BaseModel):
    name: str
    url: str
    method: str = "POST"
    headers: dict[str, str] | None = None
    transform_pipeline: list[dict] | None = None
    timeout_ms: int = 10000
    max_retries: int = 5
    retry_backoff_ms: int = 1000
    filter_expression: str | None = None

    @field_validator("filter_expression")
    @classmethod
    def check_filter(cls, v: str | None) -> str | None:
        return _validate_jmespath(v)


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
    filter_expression: str | None = None

    @field_validator("filter_expression")
    @classmethod
    def check_filter(cls, v: str | None) -> str | None:
        return _validate_jmespath(v)


class RouteOut(BaseModel):
    id: UUID
    name: str
    url: str
    method: str
    is_active: bool
    timeout_ms: int
    max_retries: int
    created_at: datetime
    filter_expression: str | None = None
    transform_pipeline: list[dict] | None = None
    headers: dict[str, str] | None = None
    retry_backoff_ms: int | None = None

    model_config = ConfigDict(from_attributes=True)


class EventOut(BaseModel):
    id: UUID
    endpoint_id: UUID
    idempotency_key: str | None
    request_body: Any
    request_headers: dict[str, str] | None = None
    status: str = "pending"
    retry_at: datetime | None = None
    received_at: datetime
    is_discarded: bool
    discarded_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DlqEventOut(BaseModel):
    event_id: UUID
    endpoint_id: UUID
    received_at: datetime
    request_body: Any
    is_discarded: bool
    discarded_at: datetime | None
    last_error: str | None
    last_status: int | None
    last_url: str | None
    total_attempts: int
    status: str = "pending"
    retry_at: datetime | None = None


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

    model_config = ConfigDict(from_attributes=True)


class WebhookResponse(BaseModel):
    status: str
    event_id: UUID
