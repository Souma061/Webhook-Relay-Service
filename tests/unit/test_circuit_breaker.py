"""
Unit tests for app/delivery/circuit_breaker.py

All Redis calls are mocked via AsyncMock so no real Redis instance is required.
Tests cover all three states (CLOSED, OPEN, HALF_OPEN) and state transitions.
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import app.core.redis as core_redis
from app.delivery.circuit_breaker import CircuitBreaker


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """Return a mock Redis client and inject it as the global singleton."""
    r = MagicMock()
    r.get = AsyncMock()
    r.set = AsyncMock()
    r.delete = AsyncMock()
    r.incr = AsyncMock()
    r.expire = AsyncMock()
    r.setnx = AsyncMock()
    core_redis.redis_client = r
    yield r
    core_redis.redis_client = None


@pytest.fixture
def cb(mock_redis):
    """Return a CircuitBreaker for a test URL with threshold=3, cooldown=30."""
    breaker = CircuitBreaker("http://example.com/webhook")
    breaker.threshold = 3
    breaker.cooldown = 30
    return breaker


# ── CLOSED state ───────────────────────────────────────────────────────────────

class TestClosedState:
    async def test_is_open_returns_false_when_no_state(self, cb, mock_redis):
        mock_redis.get.return_value = None
        assert await cb.is_open() is False

    async def test_is_open_returns_false_when_state_is_closed(self, cb, mock_redis):
        mock_redis.get.return_value = "CLOSED"
        assert await cb.is_open() is False

    async def test_record_success_clears_all_keys(self, cb, mock_redis):
        await cb.record_success()
        mock_redis.delete.assert_called_once_with(
            f"cb:{cb.url}:state",
            f"cb:{cb.url}:failures",
            f"cb:{cb.url}:open_since",
            f"cb:{cb.url}:half_tested",
        )


# ── Failure accumulation & OPEN transition ────────────────────────────────────

class TestFailureAccumulation:
    async def test_single_failure_does_not_open(self, cb, mock_redis):
        mock_redis.incr.return_value = 1  # below threshold of 3
        await cb.record_failure()
        # set() for state should NOT have been called (only expire for the counter)
        state_calls = [c for c in mock_redis.set.call_args_list if "state" in str(c)]
        assert len(state_calls) == 0

    async def test_threshold_failure_opens_circuit(self, cb, mock_redis):
        mock_redis.incr.return_value = 3  # == threshold
        await cb.record_failure()
        # circuit should now be set to OPEN
        set_calls = [str(c) for c in mock_redis.set.call_args_list]
        assert any("OPEN" in s for s in set_calls)

    async def test_failure_above_threshold_also_opens(self, cb, mock_redis):
        mock_redis.incr.return_value = 10  # > threshold
        await cb.record_failure()
        set_calls = [str(c) for c in mock_redis.set.call_args_list]
        assert any("OPEN" in s for s in set_calls)


# ── OPEN state ────────────────────────────────────────────────────────────────

class TestOpenState:
    async def test_open_circuit_blocks_requests_within_cooldown(self, cb, mock_redis):
        open_since = time.time() - 5  # opened 5 s ago, cooldown is 30 s
        mock_redis.get.side_effect = ["OPEN", str(open_since)]
        assert await cb.is_open() is True

    async def test_open_circuit_transitions_to_half_open_after_cooldown(self, cb, mock_redis):
        open_since = time.time() - 60  # opened 60 s ago, well past cooldown of 30 s
        mock_redis.get.side_effect = ["OPEN", str(open_since)]
        # Should transition to HALF_OPEN and return False (allow probe request)
        result = await cb.is_open()
        assert result is False
        mock_redis.set.assert_called_with(f"cb:{cb.url}:state", "HALF_OPEN")


# ── HALF_OPEN state ───────────────────────────────────────────────────────────

class TestHalfOpenState:
    async def test_half_open_allows_first_probe_request(self, cb, mock_redis):
        mock_redis.get.return_value = "HALF_OPEN"
        # setnx returns 1 (True) meaning the key was newly set → first probe allowed
        mock_redis.setnx.return_value = True
        result = await cb.is_open()
        assert result is False  # probe is allowed through

    async def test_half_open_blocks_subsequent_requests(self, cb, mock_redis):
        mock_redis.get.return_value = "HALF_OPEN"
        # setnx returns 0 (False) meaning key already existed → probe already in flight
        mock_redis.setnx.return_value = False
        result = await cb.is_open()
        assert result is True  # subsequent requests are blocked

    async def test_success_in_half_open_clears_all_state(self, cb, mock_redis):
        await cb.record_success()
        mock_redis.delete.assert_called_once()
        deleted_keys = mock_redis.delete.call_args[0]
        assert f"cb:{cb.url}:state" in deleted_keys
        assert f"cb:{cb.url}:failures" in deleted_keys
        assert f"cb:{cb.url}:half_tested" in deleted_keys
