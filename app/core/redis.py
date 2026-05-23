from redis.asyncio import Redis

from app.core.config import settings

redis_client: Redis | None = None


async def init_redis() -> Redis:
    global redis_client
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return redis_client


async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None


def get_redis() -> Redis:
    if redis_client is None:
        raise RuntimeError("Redis not initialized")
    return redis_client
"""
redis.py — Async Redis client lifecycle.

Manages a singleton Redis connection pool for the app.
Used by: idempotency check, rate limiter, circuit breaker.

Export:
- init_redis(): call during app startup
- close_redis(): call during app shutdown
- get_redis(): get the Redis instance (raises if not initialized)
"""
