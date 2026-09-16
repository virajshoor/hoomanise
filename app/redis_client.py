import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .config import settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is not configured")
        host = settings.redis_url.split("@")[-1].split("/")[0]
        is_local = "localhost" in host or "127.0.0.1" in host
        _redis = aioredis.from_url(
            "redis://127.0.0.1:6379/0" if (is_local and settings.redis_url.startswith("redis://localhost")) else settings.redis_url,
            max_connections=settings.redis_pool_max,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
            retry_on_timeout=True,
            retry=Retry(ExponentialBackoff(cap=0.5, base=0.05), retries=3),
            retry_on_error=[RedisConnectionError, RedisTimeoutError],
            health_check_interval=30,
        )
    return _redis


async def close_redis():
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
