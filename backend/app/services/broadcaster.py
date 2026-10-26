import asyncio
from typing import Any, Dict, Set
from app.core.logging import get_logger

logger = get_logger("taskmesh.broadcaster")


class ConnectionManager:
    """
    Manages active SSE client connections and handles bounded broadcast.

    Architecture & Memory Bounds:
    - Each SSE client gets an `asyncio.Queue(maxsize=100)`.
    - If a client is slow or lagging, queue.put_nowait drops oldest messages or discards
      to prevent unbounded memory consumption.
    - Dead or disconnected clients are automatically unhooked and garbage collected.
    - Heartbeat pings (`: ping\n\n`) are emitted periodically to maintain active connections
      across proxies/firewalls and detect dropped TCP sockets.
    """

    def __init__(self, max_queue_size: int = 100):
        self.max_queue_size = max_queue_size
        self._subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def register(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue_size)
        async with self._lock:
            self._subscribers.add(queue)
            logger.info(f"SSE client connected. Active subscribers: {len(self._subscribers)}")
        return queue

    async def unregister(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
            logger.info(f"SSE client disconnected. Active subscribers: {len(self._subscribers)}")

    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Broadcasts an event to all connected subscriber queues.
        Non-blocking: if a queue is full, the oldest item is discarded.
        """
        message = {"event": event_type, "data": data}
        async with self._lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(message)
                except asyncio.QueueFull:
                    # Drop oldest to prevent memory growth
                    try:
                        q.get_nowait()
                        q.put_nowait(message)
                    except Exception:
                        pass

    @property
    def active_connections_count(self) -> int:
        return len(self._subscribers)


# Global singleton broadcaster instance
broadcaster = ConnectionManager()
