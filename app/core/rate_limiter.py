import time
import uuid

from app.core.redis import get_redis
from app.core.config import settings


class SlidingWindowRateLimiter:
    def __init__(self):
        self.max_rpm = settings.rate_limit_rpm
        self.window_ms = 60_000

    async def allow_request(self, destination_url: str) -> bool:
        redis = get_redis()
        now = time.time() * 1000
        window_start = now - self.window_ms
        key = f"rl:{destination_url}"

        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(uuid.uuid4()): now})
        pipe.expire(key, 60)
        results = await pipe.execute()

        count = results[1]
        return (count - 1) < self.max_rpm
