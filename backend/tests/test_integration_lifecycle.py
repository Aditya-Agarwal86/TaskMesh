import asyncio
import pytest
import uuid
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import JobStatus, OutboxStatus
from app.models.job import Job, JobEvent, OutboxEvent
from app.queue.priority_queue import RedisPriorityQueue
from app.workers.dispatcher import OutboxDispatcher
from app.workers.engine import WorkerEngine
from app.workers.task_registry import default_task_registry


@pytest.mark.asyncio
async def test_full_lifecycle_queued_to_running_to_succeeded(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Submits a job via API (POST /api/v1/jobs),
    dispatches via OutboxDispatcher to Redis,
    claims and executes via WorkerEngine,
    and proves it moves from QUEUED -> RUNNING -> SUCCEEDED with result persisted.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    # 1. Submit job via API endpoint
    response = await client.post(
        "/api/v1/jobs",
        json={
            "task_type": "generate_report",
            "priority": 8,
            "payload": {
                "report_name": "Q4 Performance",
                "dataset": [{"metric": "latency", "val": 42}, {"metric": "throughput", "val": 1200}],
                "format": "summary",
            },
        },
    )
    assert response.status_code == 202
    job_data = response.json()
    job_id = uuid.UUID(job_data["id"])
    assert job_data["status"] == "QUEUED"

    # Verify initial database state
    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job_db.status == JobStatus.QUEUED.value

    outbox_event = (await db_session.execute(select(OutboxEvent).where(OutboxEvent.job_id == job_id))).scalar_one()
    assert outbox_event.status == OutboxStatus.PENDING.value

    # 2. Dispatch Outbox to Redis
    dispatcher = OutboxDispatcher(
        redis_client=fake_redis,
        session_factory=session_factory,
    )
    dispatched_count = await dispatcher.process_batch()
    assert dispatched_count == 1

    # Outbox is marked DISPATCHED
    await db_session.refresh(outbox_event)
    assert outbox_event.status == OutboxStatus.DISPATCHED.value

    # 3. Worker claims job from Redis
    queue = RedisPriorityQueue(fake_redis)
    task_item = await queue.dequeue(worker_id="test-worker-alpha", lease_timeout=30)
    assert task_item is not None
    assert task_item["job_id"] == str(job_id)

    # 4. Worker executes task
    engine = WorkerEngine(
        worker_id="test-worker-alpha",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
        lease_timeout=30,
    )
    engine.queue = queue
    await engine.process_job(task_item)

    # 5. Verify final status is SUCCEEDED with output
    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.SUCCEEDED.value
    assert job_db.worker_id == "test-worker-alpha"
    assert job_db.completed_at is not None
    assert "report_id" in job_db.result
    assert job_db.result["report_name"] == "Q4 Performance"
    assert job_db.result["record_count"] == 2

    # Verify event audit log reflects QUEUED -> RUNNING -> SUCCEEDED progression
    events = (await db_session.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
    event_types = [e.event_type for e in events]
    assert "JOB_CREATED" in event_types
    assert "JOB_STARTED" in event_types
    assert "JOB_COMPLETED" in event_types


@pytest.mark.asyncio
async def test_always_fail_task_records_errors_and_exhausts_retries(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Submits an always_fail job with max_attempts=2,
    runs worker through attempts, and verifies that structured error details
    and transitions to RETRYING and final FAILED are persisted.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    response = await client.post(
        "/api/v1/jobs",
        json={
            "task_type": "always_fail",
            "priority": 5,
            "max_attempts": 2,
            "payload": {"reason": "Database connection drop"},
        },
    )
    assert response.status_code == 202
    job_id = uuid.UUID(response.json()["id"])

    # Dispatch to Redis
    dispatcher = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    await dispatcher.process_batch()

    queue = RedisPriorityQueue(fake_redis)
    engine = WorkerEngine(
        worker_id="worker-fail-test",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
        lease_timeout=30,
    )
    engine.queue = queue

    # Attempt 1 -> Should fail and transition to RETRYING
    task_item_1 = await queue.dequeue(worker_id="worker-fail-test", lease_timeout=30)
    assert task_item_1 is not None
    await engine.process_job(task_item_1)

    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.RETRYING.value
    assert job_db.attempt_count == 1
    assert job_db.error_type == "RuntimeError"
    assert "Database connection drop" in job_db.error_message
    assert "traceback" in job_db.error_details

    # Attempt 2 -> Should fail and transition to terminal FAILED
    task_item_2 = await queue.dequeue(worker_id="worker-fail-test", lease_timeout=30)
    assert task_item_2 is not None
    await engine.process_job(task_item_2)

    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.FAILED.value
    assert job_db.attempt_count == 2
    assert job_db.completed_at is not None

    # Check event history
    events = (await db_session.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
    event_types = [e.event_type for e in events]
    assert "JOB_FAILED_RETRYING" in event_types
    assert "JOB_FAILED_TERMINAL" in event_types


@pytest.mark.asyncio
async def test_fail_once_task_recovers_on_persisted_state(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Submits a fail_once job and verifies it fails on attempt 1,
    re-executes on attempt 2 using persisted attempt_count in PostgreSQL,
    and succeeds without in-memory state.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    response = await client.post(
        "/api/v1/jobs",
        json={
            "task_type": "fail_once",
            "priority": 5,
            "max_attempts": 3,
            "payload": {},
        },
    )
    assert response.status_code == 202
    job_id = uuid.UUID(response.json()["id"])

    dispatcher = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    await dispatcher.process_batch()

    queue = RedisPriorityQueue(fake_redis)

    # First worker instance handles attempt 1
    worker_engine_1 = WorkerEngine(
        worker_id="worker-instance-1",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
    )
    worker_engine_1.queue = queue
    task_1 = await queue.dequeue(worker_id="worker-instance-1")
    await worker_engine_1.process_job(task_1)

    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.RETRYING.value
    assert job_db.attempt_count == 1

    # Completely separate worker instance handles attempt 2 to prove no memory sharing
    worker_engine_2 = WorkerEngine(
        worker_id="worker-instance-2",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
    )
    worker_engine_2.queue = queue
    task_2 = await queue.dequeue(worker_id="worker-instance-2")
    await worker_engine_2.process_job(task_2)

    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.SUCCEEDED.value
    assert job_db.attempt_count == 2
    assert job_db.result["status"] == "succeeded"
    assert job_db.result["attempt_succeeded"] == 2
    assert job_db.worker_id == "worker-instance-2"


@pytest.mark.asyncio
async def test_sleep_task_cancellation_checkpoint(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Submits a sleep task, begins execution, requests cancellation,
    and proves the worker aborts execution safely at the next checkpoint.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    response = await client.post(
        "/api/v1/jobs",
        json={
            "task_type": "sleep",
            "priority": 5,
            "payload": {"duration_seconds": 3.0, "checkpoint_interval": 0.2},
        },
    )
    assert response.status_code == 202
    job_id = uuid.UUID(response.json()["id"])

    dispatcher = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    await dispatcher.process_batch()

    queue = RedisPriorityQueue(fake_redis)
    engine = WorkerEngine(
        worker_id="worker-canceller",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
    )
    engine.queue = queue

    task = await queue.dequeue(worker_id="worker-canceller")
    assert task is not None

    # Run worker in background task
    worker_task = asyncio.create_task(engine.process_job(task))

    # Allow worker to start and enter sleep checkpoint loop
    await asyncio.sleep(0.3)

    # Cancel via API / database
    cancel_res = await client.post(f"/api/v1/jobs/{job_id}/cancel?reason=user_abort")
    assert cancel_res.status_code == 200

    # Wait for worker task to detect cancellation at checkpoint
    await worker_task

    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.CANCELLED.value

    events = (await db_session.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
    event_types = [e.event_type for e in events]
    assert "JOB_CANCELLED" in event_types
    assert "JOB_CANCELLED_IN_FLIGHT" in event_types
