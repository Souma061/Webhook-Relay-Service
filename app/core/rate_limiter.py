import time
import uuid

from app.core.redis import get_redis
from app.core.config import settings


class SlidingWindowRateLimiter:
    def __init__(self):
        self.max_rpm = settings.rate_limit_rpm
        self.window_ms = 60_000

    async def allow_request(self, key: str, max_rpm: int | None = None) -> bool:
        limit = max_rpm if max_rpm is not None else self.max_rpm
        if limit <= 0:
            return False

        redis = get_redis()
        now = time.time() * 1000
        window_start = now - self.window_ms
        rl_key = f"rl:{key}"

        pipe = redis.pipeline()
        pipe.zremrangebyscore(rl_key, 0, window_start)
        pipe.zcard(rl_key)
        pipe.zadd(rl_key, {str(uuid.uuid4()): now})
        pipe.expire(rl_key, 60)
        results = await pipe.execute()

        count = results[1]
        return count < limit
