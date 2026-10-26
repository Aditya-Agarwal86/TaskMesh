from typing import Optional
import redis.asyncio as aioredis
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("taskmesh.redis")

_redis_client: Optional[aioredis.Redis] = None


async def get_redis_client() -> aioredis.Redis:
    """Returns the shared Redis client instance, lazily initialized."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


def set_redis_client(client: Optional[aioredis.Redis]) -> None:
    """Allows setting or overriding the Redis client (e.g. for testing with fakeredis)."""
    global _redis_client
    _redis_client = client


async def close_redis_client() -> None:
    """Closes the shared Redis connection if initialized."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception as e:
            logger.warning(f"Error closing Redis client: {e}")
        finally:
            _redis_client = None
