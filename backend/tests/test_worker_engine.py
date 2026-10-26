import pytest
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import JobStatus, OutboxStatus
from app.models.job import Job, JobEvent, OutboxEvent
from app.queue.priority_queue import RedisPriorityQueue
from app.workers.dispatcher import OutboxDispatcher
from app.workers.engine import WorkerEngine
from app.workers.task_registry import TaskRegistry


@pytest.mark.asyncio
async def test_outbox_dispatcher_and_worker_pipeline(db_session: AsyncSession, fake_redis):
    # Setup mock session factory that returns the active db_session
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    # 1. Setup isolated registry
    registry = TaskRegistry()


    @registry.register("test_square")
    async def handle_square(payload):
        n = payload["number"]
        return {"squared": n * n}

    # 2. Insert a Job and OutboxEvent in DB
    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        task_type="test_square",
        payload={"number": 8},
        priority=7,
        status=JobStatus.QUEUED.value,
        attempt_count=0,
        max_attempts=3,
    )
    outbox = OutboxEvent(
        id=uuid.uuid4(),
        job_id=job_id,
        event_type="JOB_QUEUED",
        payload={"job_id": str(job_id), "task_type": "test_square", "priority": 7, "payload": {"number": 8}},
        status=OutboxStatus.PENDING.value,
    )
    db_session.add(job)
    db_session.add(outbox)
    await db_session.commit()

    # 3. Dispatch outbox to Redis
    dispatcher = OutboxDispatcher(
        redis_client=fake_redis,
        session_factory=session_factory,
    )
    dispatched_count = await dispatcher.process_batch()
    assert dispatched_count == 1

    # Verify Outbox is marked DISPATCHED in DB
    refreshed_outbox = (await db_session.execute(select(OutboxEvent).where(OutboxEvent.job_id == job_id))).scalar_one()
    assert refreshed_outbox.status == OutboxStatus.DISPATCHED.value
    assert refreshed_outbox.published_at is not None


    # 4. Run WorkerEngine to process claimed job
    queue = RedisPriorityQueue(fake_redis)
    task_item = await queue.dequeue(worker_id="test-worker-1", lease_timeout=30)
    assert task_item is not None
    assert task_item["job_id"] == str(job_id)

    engine = WorkerEngine(
        worker_id="test-worker-1",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=registry,
        lease_timeout=30,
    )
    engine.queue = queue
    await engine.process_job(task_item)

    # 5. Verify Job state in PostgreSQL is now SUCCEEDED with output
    refreshed_job = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert refreshed_job.status == JobStatus.SUCCEEDED.value
    assert refreshed_job.result == {"squared": 64}
    assert refreshed_job.completed_at is not None
    assert refreshed_job.worker_id == "test-worker-1"

    # Verify events recorded
    events = (await db_session.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
    event_types = [e.event_type for e in events]
    assert "JOB_STARTED" in event_types
    assert "JOB_COMPLETED" in event_types


@pytest.mark.asyncio
async def test_worker_retry_on_failure(db_session: AsyncSession, fake_redis):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    registry = TaskRegistry()


    @registry.register("failing_task")
    async def handle_failing(payload):
        raise RuntimeError("External dependency timeout")

    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        task_type="failing_task",
        payload={},
        priority=5,
        status=JobStatus.QUEUED.value,
        attempt_count=0,
        max_attempts=3,
    )
    db_session.add(job)
    await db_session.commit()

    queue = RedisPriorityQueue(fake_redis)
    await queue.enqueue(job_id=job_id, task_type="failing_task", priority=5)

    task_item = await queue.dequeue(worker_id="test-worker-retry", lease_timeout=30)
    assert task_item is not None

    engine = WorkerEngine(
        worker_id="test-worker-retry",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=registry,
        lease_timeout=30,
    )
    engine.queue = queue
    await engine.process_job(task_item)

    # Verify Job state transitioned to RETRYING with error details
    refreshed_job = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert refreshed_job.status == JobStatus.RETRYING.value
    assert refreshed_job.attempt_count == 1
    assert refreshed_job.error_type == "RuntimeError"
    assert "External dependency timeout" in refreshed_job.error_message
    assert refreshed_job.next_retry_at is not None
