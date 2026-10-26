import asyncio
import datetime
import signal
from typing import Optional
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.redis import get_redis_client
from app.db.session import async_session_factory
from app.models.enums import OutboxStatus
from app.models.job import OutboxEvent
from app.queue.priority_queue import RedisPriorityQueue

setup_logging()
logger = get_logger("taskmesh.dispatcher")


class OutboxDispatcher:
    """
    Polls pending PostgreSQL outbox events, enqueues them into RedisPriorityQueue,
    and marks the outbox event as DISPATCHED atomically.

    Resilience Guarantees:
      - Reads unpublished (PENDING) events from PostgreSQL.
      - Enqueues to Redis safely: Redis enqueue is idempotent and respects existing leases.
      - Updates PostgreSQL only after Redis publication succeeds.
      - If the dispatcher crashes between Redis publication and PostgreSQL update:
        On restart, the event is re-read from PostgreSQL and republished.
        Because RedisPriorityQueue.enqueue checks if the job already has an active lease or is queued,
        duplicate execution is prevented and PostgreSQL status is cleanly caught up to DISPATCHED.
      - If the dispatcher is stopped or restarted, jobs in PostgreSQL outbox are not lost and will resume.
    """

    def __init__(
        self,
        redis_client: Optional[aioredis.Redis] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        poll_interval: float = 1.0,
        batch_size: int = 50,
    ):
        self.redis_client = redis_client
        self.session_factory = session_factory or async_session_factory
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.is_running = False
        self.queue = RedisPriorityQueue(redis_client) if redis_client is not None else None

    async def _init_components(self):
        if self.redis_client is None:
            self.redis_client = await get_redis_client()
        if self.queue is None:
            self.queue = RedisPriorityQueue(self.redis_client)

    async def process_batch(self) -> int:
        """
        Processes a single batch of pending outbox events.
        Returns the number of processed events.
        """
        await self._init_components()
        assert self.queue is not None

        async with self.session_factory() as session:
            stmt = (
                select(OutboxEvent)
                .where(OutboxEvent.status == OutboxStatus.PENDING.value)
                .order_by(OutboxEvent.created_at.asc())
                .limit(self.batch_size)
            )
            result = await session.execute(stmt)
            events = list(result.scalars().all())

            if not events:
                return 0

            count = 0
            for ev in events:
                job_id = ev.job_id
                event_payload = ev.payload or {}
                task_type = event_payload.get("task_type", "default")
                priority = int(event_payload.get("priority", 5))
                task_payload = event_payload.get("payload", {})
                execution_cycle = event_payload.get("execution_cycle", 1)

                try:
                    # Publish to Redis first
                    await self.queue.enqueue(
                        job_id=job_id,
                        task_type=task_type,
                        priority=priority,
                        payload=task_payload,
                        execution_cycle=execution_cycle,
                    )

                    # Mark published in PostgreSQL only after success
                    ev.status = OutboxStatus.DISPATCHED.value
                    ev.published_at = datetime.datetime.now(datetime.timezone.utc)
                    count += 1
                except Exception as exc:
                    ev.retry_count += 1
                    ev.last_error = str(exc)
                    logger.error(f"Failed to publish outbox event {ev.id} to Redis: {exc}")

            await session.commit()
            return count

    async def start(self):
        await self._init_components()
        self.is_running = True
        logger.info(
            "Outbox Dispatcher process started",
            extra={"poll_interval": self.poll_interval, "batch_size": self.batch_size},
        )
        try:
            while self.is_running:
                dispatched = await self.process_batch()
                if dispatched == 0:
                    await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("Dispatcher cancelled; stopping...")
        finally:
            self.stop()

    def stop(self):
        self.is_running = False
        logger.info("Outbox Dispatcher process stopped")


async def main():
    dispatcher = OutboxDispatcher(
        poll_interval=settings.DISPATCHER_POLL_INTERVAL_SECONDS
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, dispatcher.stop)
        except NotImplementedError:
            pass

    await dispatcher.start()


if __name__ == "__main__":
    asyncio.run(main())
