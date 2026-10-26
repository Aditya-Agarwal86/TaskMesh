import json
from typing import Any, Dict, Optional
import redis.asyncio as aioredis
from app.core.logging import get_logger
from app.core.redis import get_redis_client
from app.services.broadcaster import broadcaster

logger = get_logger("taskmesh.notifier")

REDIS_EVENTS_CHANNEL = "taskmesh:events:notifications"


async def notify_event(
    event_type: str,
    data: Dict[str, Any],
    redis_client: Optional[aioredis.Redis] = None,
) -> None:
    """
    Publishes an event notification to:
      1. The local in-memory SSE ConnectionManager (immediate local dispatch).
      2. Redis Pub/Sub on channel `taskmesh:events:notifications` to notify other API pods/processes.

    Note: Redis Pub/Sub is used strictly as a transient signal notification mechanism,
    NOT as the authoritative event store. The authoritative state and history resides
    in PostgreSQL (`jobs` and `job_events` tables).
    """
    # 1. Local in-memory broadcast
    await broadcaster.broadcast(event_type, data)

    # 2. Redis Pub/Sub notification
    client = redis_client
    if client is None:
        try:
            client = await get_redis_client()
        except Exception:
            pass

    if client:
        try:
            payload = json.dumps({"event": event_type, "data": data})
            await client.publish(REDIS_EVENTS_CHANNEL, payload)
        except Exception as e:
            logger.debug(f"Redis pub/sub publish non-critical error: {e}")
