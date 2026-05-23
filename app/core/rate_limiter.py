import time
import uuid

from app.core.redis import get_redis
from app.core.config import settings


class SlidingWindowRateLimiter:
    def __init__(self):
        self.max_rpm = settings.rate_limit_rpm
        self.window_ms = 60_000

    async def allow_request(self, destination_url: str) -> bool:
        redis = get_redis() # Assumes Redis is initialized and available
        now = time.time() * 1000 # Current time in milliseconds
        window_start = now - self.window_ms # Start of the sliding window
        key = f"rl:{destination_url}" # Redis key for this destination

        pipe = redis.pipeline() # Atomic operations in a pipeline
        pipe.zremrangebyscore(key, 0, window_start) # Remove old entries outside the window
        pipe.zcard(key) # Count how many requests are in the current window
        pipe.zadd(key, {str(uuid.uuid4()): now}) # Add current request with a unique ID and timestamp
        pipe.expire(key, 60) # Set TTL to 60 seconds to prevent unbounded growth
        results = await pipe.execute() # Execute all commands atomically

        count = results[1] # The count of requests in the current window after cleanup
        return (count - 1) < self.max_rpm # Allow if count is less than max (subtract 1 for the current request just added)
"""
rate_limiter.py — Sliding window rate limiter per destination.

Tracks HTTP request volume per destination URL in a 60-second window.
Uses Redis sorted sets for atomic sliding window counting.

How it works:
1. Remove entries older than 60 seconds
2. Count remaining entries in the window
3. If count < limit → add current request and allow
4. If count >= limit → reject

Atomic via Redis pipeline (ZREMRANGEBYSCORE + ZCARD + ZADD in one call).
Shared across all worker instances via Redis.
"""
