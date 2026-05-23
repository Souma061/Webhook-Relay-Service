import time

from app.core.redis import get_redis
from app.core.config import settings


class CircuitBreaker:
    def __init__(self, destination_url: str):
        self.url = destination_url
        self.threshold = settings.circuit_breaker_threshold
        self.cooldown = settings.circuit_breaker_cooldown_s

    async def is_open(self) -> bool:
        redis = get_redis()
        state = await redis.get(f"cb:{self.url}:state")
        if state is None:
            return False

        if state == "OPEN":
            open_since = float(await redis.get(f"cb:{self.url}:open_since") or 0)
            if time.time() - open_since >= self.cooldown:
                await redis.set(f"cb:{self.url}:state", "HALF_OPEN")
                return False
            return True

        if state == "HALF_OPEN":
            tested = await redis.setnx(f"cb:{self.url}:half_tested", "1")
            if tested:
                return False
            return True

        return False

    async def record_success(self):
        redis = get_redis()
        await redis.delete(
            f"cb:{self.url}:state",
            f"cb:{self.url}:failures",
            f"cb:{self.url}:open_since",
            f"cb:{self.url}:half_tested",
        )

    async def record_failure(self):
        redis = get_redis()
        count = await redis.incr(f"cb:{self.url}:failures")
        await redis.expire(f"cb:{self.url}:failures", 60)

        if count >= self.threshold:
            await redis.set(f"cb:{self.url}:state", "OPEN")
            await redis.set(f"cb:{self.url}:open_since", str(time.time()))
"""
circuit_breaker.py — Circuit breaker per destination URL.

Three states:
- CLOSED: normal operation, requests pass through
- OPEN: N consecutive failures → reject all requests for cooldown period
- HALF_OPEN: after cooldown, let one request through to test recovery

Uses Redis for shared state across multiple worker instances.
State machine transitions on record_success() and record_failure().
"""
