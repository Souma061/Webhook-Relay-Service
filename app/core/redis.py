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
        try:
            await redis_client.close()
        except RuntimeError:
            pass
        redis_client = None


def get_redis() -> Redis:
    if redis_client is None:
        raise RuntimeError("Redis not initialized")
    return redis_client
