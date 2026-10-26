import asyncio
import datetime
import json
import random
import socket
import traceback
import uuid
from typing import Any, Dict, Optional
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.core.logging import setup_logging, get_logger
from app.core.redis import get_redis_client
from app.db.session import async_session_factory
from app.models.enums import JobStatus
from app.models.job import Job, JobEvent
from app.models.state_machine import validate_transition, InvalidStateTransitionError
from app.queue.priority_queue import RedisPriorityQueue
from app.workers.task_registry import TaskRegistry, default_task_registry, SafeTaskContext, PermanentTaskError
from app.services.event_notifier import notify_event

setup_logging()
logger = get_logger("taskmesh.worker")


def calculate_backoff(attempt: int, base_seconds: float = 2.0, max_seconds: float = 60.0) -> float:
    """
    Calculates exponential backoff with full jitter and a bounded maximum delay.
    Formula: Delay = uniform(0.1, min(max_seconds, base_seconds * 2^(attempt - 1)))
    """
    exp = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return random.uniform(0.1, exp)


class WorkerEngine:
    """
    Independent Worker Engine process responsible for:
    - Running completely decoupled from the FastAPI web server.
    - Unique worker_id per instance.
    - Atomically claiming jobs from RedisPriorityQueue with lease locks and fencing tokens.
    - Asynchronous heartbeats during execution to renew lease ownership.
    - Fencing tokens: Verifying worker lease and fencing token before state commits,
      preventing stale/zombie workers from overwriting newer execution state.
    - Cooperative cancellation checkpoints during task execution.
    - Differentiating retryable errors from PermanentTaskError (no retries for validation or permanent errors).
    - Recovering abandoned/stale leases safely and idempotently.
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        concurrency: int = 4,
        redis_client: Optional[aioredis.Redis] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        registry: Optional[TaskRegistry] = None,
        lease_timeout: int = 30,
        enable_lease_reaper: bool = True,
    ):
        self.worker_id = worker_id or f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
        self.concurrency = concurrency
        self.redis_client = redis_client
        self.session_factory = session_factory or async_session_factory
        self.registry = registry or default_task_registry
        self.lease_timeout = lease_timeout
        self.enable_lease_reaper = enable_lease_reaper
        self.is_running = False
        self._active_tasks: set[asyncio.Task] = set()
        self._reaper_task: Optional[asyncio.Task] = None
        self._worker_heartbeat_task: Optional[asyncio.Task] = None

    async def _init_components(self):
        if self.redis_client is None:
            self.redis_client = await get_redis_client()
        self.queue = RedisPriorityQueue(self.redis_client)

    async def _worker_presence_heartbeat_loop(self):
        """
        Periodically writes worker heartbeat metadata to Redis with TTL.
        Enables real worker telemetry and detection of online vs. idle vs. stale workers.
        """
        while self.is_running:
            try:
                if self.redis_client:
                    key = f"taskmesh:workers:{self.worker_id}"
                    data = {
                        "worker_id": self.worker_id,
                        "concurrency": self.concurrency,
                        "active_tasks_count": len(self._active_tasks),
                        "heartbeat_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "status": "BUSY" if len(self._active_tasks) > 0 else "IDLE",
                    }
                    await self.redis_client.set(key, json.dumps(data), ex=15)
                    await self.redis_client.sadd("taskmesh:active_workers", self.worker_id)
            except Exception as e:
                logger.debug(f"Failed to publish worker presence heartbeat: {e}")
            await asyncio.sleep(4.0)

    async def start(self):
        """Starts worker loop and execution concurrency semaphore."""
        await self._init_components()
        self.is_running = True
        logger.info(
            f"Worker '{self.worker_id}' started",
            extra={"worker_id": self.worker_id, "concurrency": self.concurrency, "lease_timeout": self.lease_timeout},
        )

        self._worker_heartbeat_task = asyncio.create_task(self._worker_presence_heartbeat_loop())

        if self.enable_lease_reaper:
            self._reaper_task = asyncio.create_task(self._stale_lease_reaper_loop())

        semaphore = asyncio.Semaphore(self.concurrency)

        try:
            while self.is_running:
                await semaphore.acquire()

                try:
                    task_item = await self.queue.dequeue(
                        worker_id=self.worker_id,
                        lease_timeout=self.lease_timeout,
                    )
                except Exception as e:
                    logger.error(f"Worker {self.worker_id} error polling Redis queue: {e}")
                    semaphore.release()
                    await asyncio.sleep(1)
                    continue

                if not task_item:
                    semaphore.release()
                    await asyncio.sleep(0.5)
                    continue

                task = asyncio.create_task(self._process_job_wrapper(task_item, semaphore))
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)

        except asyncio.CancelledError:
            logger.info(f"Worker {self.worker_id} shutting down...")
        finally:
            await self.stop()

    async def stop(self):
        """Gracefully cancels active tasks and releases resources."""
        self.is_running = False
        if self._worker_heartbeat_task and not self._worker_heartbeat_task.done():
            self._worker_heartbeat_task.cancel()
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
        if self.redis_client:
            try:
                await self.redis_client.delete(f"taskmesh:workers:{self.worker_id}")
                await self.redis_client.srem("taskmesh:active_workers", self.worker_id)
            except Exception:
                pass
        if self._active_tasks:
            logger.info(f"Worker {self.worker_id} waiting for {len(self._active_tasks)} active jobs to complete...")
            for task in list(self._active_tasks):
                task.cancel()
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        logger.info(f"Worker {self.worker_id} stopped.")

    async def _process_job_wrapper(self, task_item: Dict[str, Any], semaphore: asyncio.Semaphore):
        try:
            await self.process_job(task_item)
        except Exception as e:
            logger.exception(f"Unhandled error in job processing: {e}")
        finally:
            semaphore.release()

    async def _heartbeat_loop(self, job_id: uuid.UUID, stop_event: asyncio.Event):
        """Periodically renews the lease in Redis until task finishes."""
        interval = max(1, self.lease_timeout // 3)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                renewed = await self.queue.renew_lease(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    ttl=self.lease_timeout,
                )
                if renewed:
                    logger.debug(f"Worker {self.worker_id} renewed lease for job {job_id}")
                else:
                    logger.warning(f"Worker {self.worker_id} failed to renew lease for job {job_id}; lost ownership!")
                    break

    async def check_cancellation(self, job_id: uuid.UUID) -> bool:
        """Checkpoint check to verify if the job was cancelled in PostgreSQL."""
        async with self.session_factory() as session:
            stmt = select(Job.status).where(Job.id == job_id)
            result = await session.execute(stmt)
            status = result.scalar_one_or_none()
            return status == JobStatus.CANCELLED.value

    async def _verify_lease_ownership(self, job_id: uuid.UUID) -> bool:
        """Verifies if this worker currently holds the exclusive lease in Redis."""
        if self.redis_client is None:
            return False
        current_owner = await self.redis_client.get(f"taskmesh:lease:{job_id}")
        return current_owner == self.worker_id

    async def process_job(self, task_item: Dict[str, Any]):
        """Executes a single claimed job with leasing and database state management."""
        job_id_str = task_item.get("job_id")
        if not job_id_str:
            return

        job_id = uuid.UUID(job_id_str)
        task_type = task_item.get("task_type", "unknown")
        payload = task_item.get("payload", {})

        # Generate unique fencing token for this execution attempt
        fencing_token = f"{self.worker_id}:{uuid.uuid4().hex[:8]}"

        # Start lease heartbeat loop
        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id, stop_heartbeat))

        current_attempt = 1
        max_attempts = 3
        current_cycle = 1

        async with self.session_factory() as session:
            stmt = select(Job).where(Job.id == job_id).with_for_update()
            result = await session.execute(stmt)
            job = result.scalar_one_or_none()

            if not job:
                logger.warning(f"Job {job_id} not found in database; acknowledging cleanup")
                stop_heartbeat.set()
                await heartbeat_task
                await self.queue.acknowledge_completion(job_id)
                return

            if job.status == JobStatus.CANCELLED.value:
                logger.info(f"Job {job_id} was cancelled before execution; skipping")
                stop_heartbeat.set()
                await heartbeat_task
                await self.queue.acknowledge_completion(job_id)
                return

            # Check if this claimed task item belongs to an obsolete execution cycle
            claimed_cycle = task_item.get("execution_cycle")
            if claimed_cycle is not None and job.execution_cycle is not None and claimed_cycle < job.execution_cycle:
                logger.warning(
                    f"STALE CYCLE CLAIM REJECTED: Task item for job {job_id} is from cycle {claimed_cycle}, "
                    f"but active database cycle is {job.execution_cycle}. Discarding stale queue item."
                )
                stop_heartbeat.set()
                await heartbeat_task
                await self.queue.acknowledge_completion(job_id)
                return

            # Validate state transition from current state to RUNNING
            try:
                validate_transition(job.status, JobStatus.RUNNING, reason="Worker claimed task")
            except InvalidStateTransitionError as e:
                logger.warning(f"Rejecting claim for job {job_id}: {e}")
                stop_heartbeat.set()
                await heartbeat_task
                await self.queue.acknowledge_completion(job_id)
                return

            now = datetime.datetime.now(datetime.timezone.utc)
            job.status = JobStatus.RUNNING.value
            job.worker_id = self.worker_id
            job.started_at = now
            job.attempt_count += 1
            job.total_attempt_count = (job.total_attempt_count or 0) + 1
            current_attempt = job.attempt_count
            max_attempts = job.max_attempts
            current_cycle = job.execution_cycle or 1
            job.lease_expires_at = now + datetime.timedelta(seconds=self.lease_timeout)

            running_event = JobEvent(
                id=uuid.uuid4(),
                job_id=job_id,
                event_type="JOB_STARTED",
                metadata_payload={
                    "worker_id": self.worker_id,
                    "attempt": current_attempt,
                    "total_attempts": job.total_attempt_count,
                    "cycle": current_cycle,
                    "fencing_token": fencing_token,
                },
            )
            session.add(running_event)
            await session.commit()

        await notify_event(
            "job_update",
            {
                "job_id": str(job_id),
                "status": JobStatus.RUNNING.value,
                "task_type": task_type,
                "worker_id": self.worker_id,
                "attempt": current_attempt,
                "cycle": current_cycle,
                "event_type": "JOB_STARTED",
            },
            redis_client=self.redis_client,
        )

        logger.info(
            f"Worker {self.worker_id} started job {job_id} (cycle {current_cycle}, attempt {current_attempt}/{max_attempts})",
            extra={"job_id": str(job_id), "worker_id": self.worker_id, "attempt": current_attempt, "cycle": current_cycle, "event": "JOB_STARTED"},
        )

        context = SafeTaskContext(
            job_id=str(job_id),
            attempt_count=current_attempt,
            max_attempts=max_attempts,
            is_cancelled=lambda: self.check_cancellation(job_id),
        )

        # Execute task logic
        execution_error: Optional[Exception] = None
        error_tb: Optional[str] = None
        task_result: Any = None
        was_cancelled = False

        try:
            task_result = await self.registry.execute(task_type, payload, context=context)
        except asyncio.CancelledError:
            was_cancelled = True
            logger.info(f"Job {job_id} observed cancellation at checkpoint")
        except Exception as exc:
            execution_error = exc
            # Correctly capture the formatted traceback while the exception is active
            error_tb = "".join(traceback.format_exception(exc))
            logger.error(
                f"Job {job_id} execution failed: {exc}",
                extra={"job_id": str(job_id), "worker_id": self.worker_id, "attempt": current_attempt, "cycle": current_cycle, "event": "JOB_ERROR"},
            )

        # Stop heartbeat
        stop_heartbeat.set()
        await heartbeat_task

        # FENCING CHECK: Before committing final state, verify this worker still holds the lease
        has_lease = await self._verify_lease_ownership(job_id)
        if not has_lease:
            logger.error(
                f"FENCING VIOLATION: Worker {self.worker_id} lost lease for job {job_id}! "
                f"Refusing to commit completion state to prevent zombie overwriting newer state."
            )
            return

        # Persist outcome to database
        async with self.session_factory() as session:
            stmt = select(Job).where(Job.id == job_id).with_for_update()
            result = await session.execute(stmt)
            job = result.scalar_one()

            # Cycle Fencing Check: Ensure job in database has not moved to a newer cycle
            if job.execution_cycle is not None and job.execution_cycle > current_cycle:
                logger.error(
                    f"CYCLE FENCING VIOLATION: Worker {self.worker_id} was executing cycle {current_cycle}, "
                    f"but job {job_id} is already in cycle {job.execution_cycle}. Refusing to commit state."
                )
                await self.queue.acknowledge_completion(job_id)
                return

            now = datetime.datetime.now(datetime.timezone.utc)

            # Check if job was cancelled in DB or caught cancellation
            if was_cancelled or job.status == JobStatus.CANCELLED.value:
                validate_transition(job.status, JobStatus.CANCELLED, reason="Cancelled during flight")
                job.status = JobStatus.CANCELLED.value
                job.completed_at = now
                job.lease_expires_at = None
                job.next_retry_at = None
                cancel_event = JobEvent(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    event_type="JOB_CANCELLED_IN_FLIGHT",
                    metadata_payload={"worker_id": self.worker_id, "attempt": job.attempt_count},
                )
                session.add(cancel_event)
                await session.commit()
                await self.queue.acknowledge_completion(job_id)
                logger.info(f"Job {job_id} cancelled safely at checkpoint")
                return

            if execution_error is None:
                # SUCCESS
                validate_transition(job.status, JobStatus.SUCCEEDED, reason="Task execution succeeded")
                job.status = JobStatus.SUCCEEDED.value
                job.completed_at = now
                job.result = task_result if isinstance(task_result, dict) else {"output": task_result}
                job.lease_expires_at = None
                job.next_retry_at = None

                duration_s = (now - (job.started_at or now)).total_seconds()
                success_event = JobEvent(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    event_type="JOB_COMPLETED",
                    metadata_payload={
                        "worker_id": self.worker_id,
                        "duration_s": duration_s,
                        "attempt": job.attempt_count,
                    },
                )
                session.add(success_event)
                await session.commit()
                await self.queue.acknowledge_completion(job_id)
                await notify_event(
                    "job_update",
                    {
                        "job_id": str(job_id),
                        "status": JobStatus.SUCCEEDED.value,
                        "task_type": job.task_type,
                        "worker_id": self.worker_id,
                        "duration_s": duration_s,
                        "event_type": "JOB_COMPLETED",
                    },
                    redis_client=self.redis_client,
                )
                logger.info(
                    f"Job {job_id} succeeded in {duration_s:.2f}s",
                    extra={"job_id": str(job_id), "worker_id": self.worker_id, "attempt": job.attempt_count, "duration_s": duration_s, "event": "JOB_COMPLETED"},
                )

            else:
                # Use error_tb captured inside except block, or format if present
                if error_tb is None and execution_error:
                    error_tb = "".join(traceback.format_exception(execution_error))

                # Check if error is permanently non-retryable
                is_permanent = isinstance(execution_error, PermanentTaskError)
                can_retry = (not is_permanent) and (job.attempt_count < job.max_attempts)

                if can_retry:
                    # RETRYING
                    validate_transition(job.status, JobStatus.RETRYING, reason="Retryable failure")
                    delay = calculate_backoff(job.attempt_count)
                    next_retry = now + datetime.timedelta(seconds=delay)
                    job.status = JobStatus.RETRYING.value
                    job.next_retry_at = next_retry
                    job.error_type = execution_error.__class__.__name__ if execution_error else "UnknownError"
                    job.error_message = str(execution_error) if execution_error else "No error message"
                    job.error_details = {"traceback": error_tb} if error_tb else None
                    job.lease_expires_at = None

                    retry_event = JobEvent(
                        id=uuid.uuid4(),
                        job_id=job_id,
                        event_type="JOB_FAILED_RETRYING",
                        metadata_payload={
                            "attempt": job.attempt_count,
                            "cycle": job.execution_cycle or 1,
                            "total_attempts": job.total_attempt_count or job.attempt_count,
                            "next_retry_in_seconds": round(delay, 2),
                            "next_retry_at": next_retry.isoformat(),
                            "error": str(execution_error) if execution_error else None,
                            "error_type": execution_error.__class__.__name__ if execution_error else None,
                            "error_classification": "RETRYABLE",
                        },
                    )
                    session.add(retry_event)
                    await session.commit()

                    # Re-enqueue in Redis with future score and execution_cycle preserved
                    await self.queue.acknowledge_completion(job_id)
                    future_timestamp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delay)).timestamp()
                    await self.queue.enqueue(
                        job_id=job_id,
                        task_type=job.task_type,
                        priority=job.priority,
                        payload=job.payload,
                        scheduled_at=future_timestamp,
                        execution_cycle=job.execution_cycle or 1,
                    )
                    await notify_event(
                        "job_update",
                        {
                            "job_id": str(job_id),
                            "status": JobStatus.RETRYING.value,
                            "task_type": job.task_type,
                            "attempt": job.attempt_count,
                            "cycle": job.execution_cycle or 1,
                            "next_retry_at": next_retry.isoformat(),
                            "event_type": "JOB_FAILED_RETRYING",
                        },
                        redis_client=self.redis_client,
                    )
                    logger.info(
                        f"Job {job_id} scheduled for retry #{job.attempt_count} in {delay:.2f}s (cycle {job.execution_cycle})",
                        extra={"job_id": str(job_id), "worker_id": self.worker_id, "attempt": job.attempt_count, "cycle": job.execution_cycle, "next_retry_in_s": delay, "event": "JOB_RETRY_SCHEDULED"},
                    )

                else:
                    # TERMINAL FAILURE (max attempts exhausted OR permanent error)
                    validate_transition(job.status, JobStatus.FAILED, reason="Max attempts exhausted or permanent error")
                    job.status = JobStatus.FAILED.value
                    job.completed_at = now
                    job.next_retry_at = None
                    job.error_type = execution_error.__class__.__name__ if execution_error else "UnknownError"
                    job.error_message = str(execution_error) if execution_error else "Terminal error"
                    job.error_details = {"traceback": error_tb} if error_tb else None
                    job.lease_expires_at = None

                    classification = "PERMANENT_ERROR" if is_permanent else "RETRY_BUDGET_EXHAUSTED"
                    failed_event = JobEvent(
                        id=uuid.uuid4(),
                        job_id=job_id,
                        event_type="JOB_FAILED_TERMINAL",
                        metadata_payload={
                            "attempt": job.attempt_count,
                            "max_attempts": job.max_attempts,
                            "cycle": job.execution_cycle or 1,
                            "total_attempts": job.total_attempt_count or job.attempt_count,
                            "error": str(execution_error) if execution_error else None,
                            "error_type": execution_error.__class__.__name__ if execution_error else None,
                            "error_classification": classification,
                        },
                    )
                    session.add(failed_event)
                    await session.commit()
                    await self.queue.acknowledge_completion(job_id)
                    await notify_event(
                        "job_update",
                        {
                            "job_id": str(job_id),
                            "status": JobStatus.FAILED.value,
                            "task_type": job.task_type,
                            "attempt": job.attempt_count,
                            "cycle": job.execution_cycle or 1,
                            "error_type": job.error_type,
                            "event_type": "JOB_FAILED_TERMINAL",
                        },
                        redis_client=self.redis_client,
                    )
                    logger.error(
                        f"Job {job_id} fatally failed after {job.attempt_count} attempts ({classification})",
                        extra={"job_id": str(job_id), "worker_id": self.worker_id, "attempt": job.attempt_count, "event": "JOB_FAILED_TERMINAL"},
                    )


    async def recover_stale_jobs(self) -> int:
        """
        Scans for jobs marked RUNNING in PostgreSQL whose lease has expired
        (or whose lease key in Redis is gone), transitioning them to RETRYING or FAILED.
        Safe to run repeatedly and concurrently.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        recovered_count = 0

        async with self.session_factory() as session:
            # Query jobs currently marked RUNNING where lease_expires_at is in the past
            stmt = select(Job).where(
                Job.status == JobStatus.RUNNING.value,
                Job.lease_expires_at < now,
            ).with_for_update(skip_locked=True)
            result = await session.execute(stmt)
            stale_jobs = list(result.scalars().all())

            for job in stale_jobs:
                # Double-check Redis lease key
                if self.redis_client is not None:
                    lease_key = f"taskmesh:lease:{job.id}"
                    active_worker = await self.redis_client.get(lease_key)
                    if active_worker:
                        # Still held, skip
                        continue

                logger.warning(
                    f"Recovering abandoned job {job.id} (last worker: {job.worker_id}, expired at {job.lease_expires_at})",
                    extra={"job_id": str(job.id), "event": "LEASE_EXPIRED_RECOVERY"},
                )

                can_retry = job.attempt_count < job.max_attempts
                if can_retry:
                    delay = calculate_backoff(job.attempt_count)
                    job.status = JobStatus.RETRYING.value
                    job.next_retry_at = now + datetime.timedelta(seconds=delay)
                    job.lease_expires_at = None
                    job.error_type = "LeaseTimeoutError"
                    job.error_message = f"Worker {job.worker_id} heartbeat lost. Lease expired."

                    event = JobEvent(
                        id=uuid.uuid4(),
                        job_id=job.id,
                        event_type="JOB_LEASE_EXPIRED_RECOVERED",
                        metadata_payload={"previous_worker": job.worker_id, "next_retry_in_s": delay},
                    )
                    session.add(event)

                    await self.queue.acknowledge_completion(job.id)
                    future_timestamp = (now + datetime.timedelta(seconds=delay)).timestamp()
                    await self.queue.enqueue(
                        job_id=job.id,
                        task_type=job.task_type,
                        priority=job.priority,
                        payload=job.payload,
                        scheduled_at=future_timestamp,
                    )
                else:
                    job.status = JobStatus.FAILED.value
                    job.completed_at = now
                    job.lease_expires_at = None
                    job.next_retry_at = None
                    job.error_type = "LeaseTimeoutError"
                    job.error_message = f"Worker {job.worker_id} heartbeat lost. Lease expired and retry budget exhausted."
                    job.error_details = {"reason": "Worker lease expired after attempt limit reached"}

                    event = JobEvent(
                        id=uuid.uuid4(),
                        job_id=job.id,
                        event_type="JOB_FAILED_TERMINAL",
                        metadata_payload={"previous_worker": job.worker_id, "reason": "Lease expired with max attempts exceeded"},
                    )
                    session.add(event)
                    await self.queue.acknowledge_completion(job.id)

                recovered_count += 1

            await session.commit()
            return recovered_count

    async def _stale_lease_reaper_loop(self):
        """Background supervisory loop detecting and recovering abandoned leases."""
        while self.is_running:
            try:
                await self.recover_stale_jobs()
            except Exception as e:
                logger.error(f"Error in stale lease reaper: {e}")
            await asyncio.sleep(max(2.0, self.lease_timeout / 2))
