"""
Unit tests for app/delivery/worker.py (_deliver_with_retry).

All external I/O is mocked:
  - Redis (via CircuitBreaker + RateLimiter injected mocks)
  - httpx.AsyncClient
  - PostgreSQL (async_session_factory)
  - Kafka producer (get_kafka)

Tests verify retry logic, circuit breaker integration, rate limiter
integration, DLQ publish on exhaustion, and audit log writes.
"""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

import app.core.redis as core_redis
from app.delivery.worker import _deliver_with_retry


# ── Shared test data ───────────────────────────────────────────────────────────

ROUTE = {
    "id": uuid.uuid4(),
    "url": "http://dest.example.com/hook",
    "method": "POST",
    "headers": {},
    "timeout_ms": 5000,
    "max_retries": 3,
    "retry_backoff_ms": 100,
}
EVENT_ID = uuid.uuid4()
BODY = {"event": "payment.succeeded", "amount": 100}


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_redis_client():
    """Provide a mock Redis so CB and rate limiter don't blow up."""
    r = MagicMock()
    r.get = AsyncMock(return_value=None)          # CB: CLOSED
    r.set = AsyncMock()
    r.delete = AsyncMock()
    r.incr = AsyncMock(return_value=0)
    r.expire = AsyncMock()
    r.setnx = AsyncMock(return_value=True)

    pipeline = MagicMock()
    pipeline.zremrangebyscore = MagicMock()
    pipeline.zcard = MagicMock()
    pipeline.zadd = MagicMock()
    pipeline.expire = MagicMock()
    pipeline.execute = AsyncMock(return_value=[0, 0, 1, True])  # rate limiter: allow
    r.pipeline.return_value = pipeline
    core_redis.redis_client = r
    yield r
    core_redis.redis_client = None


@pytest.fixture(autouse=True)
def mock_url_validator():
    """Skip the SSRF/DNS check in unit tests — it's tested separately.

    The test ROUTE uses http:// which would be rejected by validate_delivery_url_for_request.
    Unit tests focus on retry/audit/DLQ logic, not URL security enforcement.
    """
    with patch(
        "app.delivery.worker.validate_delivery_url_for_request",
        return_value=None,
    ):
        yield


@pytest.fixture
def mock_db_session():
    """Mock async DB session so DeliveryAttempt inserts are no-ops."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.delivery.worker.async_session_factory", return_value=ctx):
        yield session


@pytest.fixture
def mock_kafka():
    """Mock get_kafka() so DLQ publish doesn't need a real broker.
    
    get_kafka is imported lazily inside _deliver_with_retry (inside the function
    body), so we patch it at the app.core.kafka module level, not at worker level.
    """
    producer = MagicMock()
    producer.send_and_wait = AsyncMock()
    with patch("app.core.kafka.get_kafka", return_value=producer):
        yield producer


# ── Successful delivery ────────────────────────────────────────────────────────

class TestSuccessfulDelivery:
    async def test_successful_200_writes_audit_and_returns(self, mock_db_session, mock_kafka):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _deliver_with_retry(EVENT_ID, ROUTE, BODY, attempt=0)

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()
        # No DLQ publish on success
        mock_kafka.send_and_wait.assert_not_called()

    async def test_201_response_also_treated_as_success(self, mock_db_session, mock_kafka):
        mock_resp = MagicMock(status_code=201, text="Created")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _deliver_with_retry(EVENT_ID, ROUTE, BODY, attempt=0)

        mock_kafka.send_and_wait.assert_not_called()


# ── Client errors (4xx) ────────────────────────────────────────────────────────

class TestClientErrors:
    async def test_400_does_not_retry(self, mock_db_session, mock_kafka):
        """4xx responses are not retried — bad config, retrying won't help."""
        mock_resp = MagicMock(status_code=400, text="Bad Request")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _deliver_with_retry(EVENT_ID, ROUTE, BODY, attempt=0)

        # Only one audit row, no DLQ
        assert mock_db_session.add.call_count == 1
        mock_kafka.send_and_wait.assert_not_called()

    async def test_404_does_not_retry(self, mock_db_session, mock_kafka):
        mock_resp = MagicMock(status_code=404, text="Not Found")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _deliver_with_retry(EVENT_ID, ROUTE, BODY, attempt=0)

        assert mock_db_session.add.call_count == 1


# ── Server errors (5xx) & retry logic ─────────────────────────────────────────

class TestRetryLogic:
    async def test_503_retries_up_to_max_retries(self, mock_db_session, mock_kafka):
        """3 consecutive 503s should exhaust retries and publish to DLQ."""
        mock_resp = MagicMock(status_code=503, text="Unavailable")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        route = {**ROUTE, "max_retries": 3, "retry_backoff_ms": 1}

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _deliver_with_retry(EVENT_ID, route, BODY, attempt=0)

        # max_retries=3 means attempts 0,1,2 → 3 audit rows
        assert mock_db_session.add.call_count == 3
        mock_kafka.send_and_wait.assert_called_once()
        dlq_msg = mock_kafka.send_and_wait.call_args[1]["value"]
        assert dlq_msg["event_id"] == str(EVENT_ID)
        assert dlq_msg["attempts"] == 3

    async def test_eventual_success_stops_retrying(self, mock_db_session, mock_kafka):
        """Fail twice then succeed — should not publish to DLQ."""
        responses = [
            MagicMock(status_code=503, text="Err"),
            MagicMock(status_code=503, text="Err"),
            MagicMock(status_code=200, text="OK"),
        ]
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=responses)

        route = {**ROUTE, "max_retries": 5, "retry_backoff_ms": 1}

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _deliver_with_retry(EVENT_ID, route, BODY, attempt=0)

        assert mock_db_session.add.call_count == 3
        mock_kafka.send_and_wait.assert_not_called()


# ── Circuit breaker integration ────────────────────────────────────────────────

class TestCircuitBreakerIntegration:
    async def test_open_circuit_breaker_skips_http_call(self, mock_db_session, mock_kafka, mock_redis_client):
        """When CB is OPEN, the HTTP call should be skipped and an audit row written."""
        # CB returns OPEN within cooldown
        import time
        mock_redis_client.get.side_effect = ["OPEN", str(time.time() - 5)]

        route = {**ROUTE, "max_retries": 1, "retry_backoff_ms": 1}

        with patch("httpx.AsyncClient") as mock_httpx, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _deliver_with_retry(EVENT_ID, route, BODY, attempt=0)
            # HTTP should never be called
            mock_httpx.assert_not_called()

        mock_db_session.add.assert_called()
        audit = mock_db_session.add.call_args[0][0]
        assert audit.error == "circuit_breaker_open"


# ── Rate limiter integration ───────────────────────────────────────────────────

class TestRateLimiterIntegration:
    async def test_rate_limited_request_abandoned_after_max_waits(self, mock_db_session, mock_kafka, mock_redis_client):
        """After MAX_RATE_LIMIT_WAITS consecutive denials, we abandon and write audit row."""
        # Rate limiter: deny always (count > max_rpm)
        pipeline = mock_redis_client.pipeline.return_value
        pipeline.execute = AsyncMock(return_value=[0, 9999, 1, True])  # count 9999 >> limit

        route = {**ROUTE, "max_retries": 3, "retry_backoff_ms": 1}

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _deliver_with_retry(EVENT_ID, route, BODY, attempt=0, rate_limit_waits=5)

        audit = mock_db_session.add.call_args[0][0]
        assert audit.error == "rate_limited_abandoned"
