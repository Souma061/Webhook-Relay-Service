"""
Unit tests for app/core/rate_limiter.py

All Redis calls are mocked via AsyncMock. Tests verify that the sliding-window
rate limiter correctly allows/denies requests based on the count returned from
the Redis pipeline.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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
    rl.max_rpm = 10  # 10 requests per minute for test predictability
    return rl


# ── allow_request ──────────────────────────────────────────────────────────────

class TestAllowRequest:
    async def test_allows_when_count_below_limit(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        # pipeline.execute() returns [removed_count, current_count, zadd_result, expire_result]
        # current_count = 5 (index 1), max_rpm = 10
        pipeline.execute.return_value = [0, 5, 1, True]
        result = await limiter.allow_request("http://example.com")
        assert result is True

    async def test_allows_exactly_at_limit_minus_one(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        # count = 10, max_rpm = 10; formula: (10 - 1) < 10 → True
        pipeline.execute.return_value = [0, 10, 1, True]
        result = await limiter.allow_request("http://example.com")
        assert result is True

    async def test_denies_when_count_equals_limit(self, limiter, mock_redis_pipeline):
        _, pipeline = mock_redis_pipeline
        # count = 11, max_rpm = 10; formula: (11 - 1) < 10 → False
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
        # count = 1 because the ZADD just added the current request
        pipeline.execute.return_value = [0, 1, 1, True]
        result = await limiter.allow_request("http://example.com")
        assert result is True

    async def test_pipeline_commands_called(self, limiter, mock_redis_pipeline):
        """Verify the pipeline executes the four expected Redis commands."""
        _, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 3, 1, True]
        await limiter.allow_request("http://dest.com")
        pipeline.zremrangebyscore.assert_called_once()
        pipeline.zcard.assert_called_once()
        pipeline.zadd.assert_called_once()
        pipeline.expire.assert_called_once()
        pipeline.execute.assert_called_once()

    async def test_different_urls_use_different_keys(self, limiter, mock_redis_pipeline):
        """Each destination URL gets its own Redis sorted-set key."""
        redis_mock, pipeline = mock_redis_pipeline
        pipeline.execute.return_value = [0, 1, 1, True]

        await limiter.allow_request("http://a.example.com")
        await limiter.allow_request("http://b.example.com")

        zadd_calls = pipeline.zadd.call_args_list
        keys = [str(c) for c in zadd_calls]
        assert any("http://a.example.com" in k for k in keys)
        assert any("http://b.example.com" in k for k in keys)
