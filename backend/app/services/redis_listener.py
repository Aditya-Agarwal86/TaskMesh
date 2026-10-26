import asyncio
import json
from typing import Optional
import redis.asyncio as aioredis
from app.core.logging import get_logger
from app.core.redis import get_redis_client
from app.services.broadcaster import broadcaster
from app.services.event_notifier import REDIS_EVENTS_CHANNEL

logger = get_logger("taskmesh.redis_listener")

_listener_task: Optional[asyncio.Task] = None


async def redis_pubsub_listener_loop():
    """
    Subscribes to Redis Pub/Sub channel `taskmesh:events:notifications`
    and relays events into local in-memory SSE subscriber queues.
    This enables multi-process API workers to receive events emitted by workers or other API nodes.
    """
    client: Optional[aioredis.Redis] = None
    pubsub = None
    try:
        client = await get_redis_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(REDIS_EVENTS_CHANNEL)
        logger.info(f"Subscribed to Redis Pub/Sub channel '{REDIS_EVENTS_CHANNEL}'")

        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    raw_data = message.get("data")
                    if raw_data:
                        parsed = json.loads(raw_data)
                        event_type = parsed.get("event", "message")
                        event_data = parsed.get("data", {})
                        # Dispatch into local SSE client queues
                        await broadcaster.broadcast(event_type, event_data)
                else:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Redis listener loop temporary error: {e}")
                await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"Failed to start Redis Pub/Sub listener: {e}")
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(REDIS_EVENTS_CHANNEL)
                await pubsub.aclose()
            except Exception:
                pass


def start_redis_pubsub_listener():
    global _listener_task
    if _listener_task is None or _listener_task.done():
        _listener_task = asyncio.create_task(redis_pubsub_listener_loop())


def stop_redis_pubsub_listener():
    global _listener_task
    if _listener_task and not _listener_task.done():
        _listener_task.cancel()
        _listener_task = None
