"""
Unit tests for app/core/rate_limiter.py

All Redis calls are mocked via AsyncMock. Tests verify that the sliding-window
rate limiter correctly allows/denies requests based on the count returned from
the Redis pipeline.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

import app.core.redis as core_redis
from app.core.rate_limiter import SlidingWindowRateLimiter


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis_pipeline():
    """
    Inject a mock Redis client whose pipeline() returns an async-capable mock.
    Returns (redis_mock, pipeline_mock) for fine-grained assertions.
    """
    pipeline_mock = MagicMock()
    pipeline_mock.zremrangebyscore = MagicMock()
    pipeline_mock.zcard = MagicMock()
    pipeline_mock.zadd = MagicMock()
    pipeline_mock.expire = MagicMock()
    pipeline_mock.execute = AsyncMock()

    redis_mock = MagicMock()
    redis_mock.pipeline.return_value = pipeline_mock

    core_redis.redis_client = redis_mock
    yield redis_mock, pipeline_mock
    core_redis.redis_client = None


@pytest.fixture
def limiter():
    rl = SlidingWindowRateLimiter()
    rl.max_rpm = 10
    return rl


# ── allow_request ──────────────────────────────────────────────────────────────

class TestAllowRequest:
    async def test_allows_when_count_below_limit(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 5, 1, True]
        result = await limiter.allow_request("http://example.com")
        assert result is True

    async def test_allows_exactly_at_limit_minus_one(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 9, 1, True]
        result = await limiter.allow_request("http://example.com")
        assert result is True

    async def test_denies_when_count_equals_limit(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 11, 1, True]
        result = await limiter.allow_request("http://example.com")
        assert result is False

    async def test_denies_when_count_far_exceeds_limit(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 100, 1, True]
        result = await limiter.allow_request("http://example.com")
        assert result is False

    async def test_allows_at_zero_requests(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 1, 1, True]
        result = await limiter.allow_request("http://example.com")
        assert result is True

    async def test_pipeline_commands_called(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 3, 1, True]
        await limiter.allow_request("http://dest.com")
        pipeline.zremrangebyscore.assert_called_once()
        pipeline.zcard.assert_called_once()
        pipeline.zadd.assert_called_once()
        pipeline.expire.assert_called_once()
        pipeline.execute.assert_called_once()

    async def test_different_keys_use_different_redis_keys(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 1, 1, True]

        await limiter.allow_request("key-a")
        await limiter.allow_request("key-b")

        zadd_calls = pipeline.zadd.call_args_list
        keys = [str(c) for c in zadd_calls]
        assert any("key-a" in k for k in keys)
        assert any("key-b" in k for k in keys)

    async def test_custom_max_rpm_overrides_default(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 3, 1, True]
        # default is 10, passing 5 as custom limit — 3 < 5 → True
        result = await limiter.allow_request("custom-key", max_rpm=5)
        assert result is True

    async def test_custom_max_rpm_stricter_than_default(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 6, 1, True]
        # default is 10, passing 5 as custom limit — 6 < 5 → False
        result = await limiter.allow_request("strict-key", max_rpm=5)
        assert result is False

    async def test_custom_max_rpm_allows_burst(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 60, 1, True]
        result = await limiter.allow_request("burst-key", max_rpm=100)
        assert result is True

    async def test_zero_max_rpm_denies_all(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 1, 1, True]
        result = await limiter.allow_request("blocked-key", max_rpm=0)
        assert result is False
