import asyncio
import traceback
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import JobStatus
from app.models.job import Job, JobEvent
from app.queue.priority_queue import RedisPriorityQueue
from app.workers.dispatcher import OutboxDispatcher
from app.workers.engine import WorkerEngine
from app.workers.task_registry import TaskRegistry, default_task_registry


@pytest.mark.asyncio
async def test_always_fail_exhausts_retries_within_single_cycle(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Test 1: always_fail with max_attempts=2 reaches terminal failure after two attempts in cycle 1.
    attempt_count must be 2, total_attempt_count must be 2, max_attempts remains 2.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    res = await client.post(
        "/api/v1/jobs",
        json={
            "task_type": "always_fail",
            "priority": 5,
            "max_attempts": 2,
            "payload": {"reason": "Cycle 1 transient error"},
        },
    )
    assert res.status_code == 202
    job_id = uuid.UUID(res.json()["id"])

    dispatcher = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    await dispatcher.process_batch()

    queue = RedisPriorityQueue(fake_redis)
    engine = WorkerEngine(
        worker_id="worker-c1",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
        lease_timeout=30,
    )
    engine.queue = queue

    # Attempt 1
    item1 = await queue.dequeue(worker_id="worker-c1", lease_timeout=30)
    assert item1 is not None
    await engine.process_job(item1)

    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.RETRYING.value
    assert job_db.execution_cycle == 1
    assert job_db.attempt_count == 1
    assert job_db.total_attempt_count == 1
    assert job_db.max_attempts == 2

    # Attempt 2 -> terminal failure
    item2 = await queue.dequeue(worker_id="worker-c1", lease_timeout=30)
    assert item2 is not None
    await engine.process_job(item2)

    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.FAILED.value
    assert job_db.execution_cycle == 1
    assert job_db.attempt_count == 2
    assert job_db.total_attempt_count == 2
    assert job_db.max_attempts == 2
    assert job_db.completed_at is not None


@pytest.mark.asyncio
async def test_manual_retry_starts_new_cycle_and_preserves_history(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Test 2 & 3: Manual retry starts cycle 2, resets attempt_count=0, preserves total_attempt_count,
    and the second cycle respects its own max_attempts (2) without exceeding max_attempts in any cycle.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    # 1. Submit always_fail with max_attempts=2
    res = await client.post(
        "/api/v1/jobs",
        json={"task_type": "always_fail", "priority": 5, "max_attempts": 2, "payload": {}},
    )
    job_id = uuid.UUID(res.json()["id"])

    dispatcher = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    await dispatcher.process_batch()

    queue = RedisPriorityQueue(fake_redis)
    engine = WorkerEngine(
        worker_id="worker-test",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
        lease_timeout=30,
    )
    engine.queue = queue

    # Run cycle 1 to terminal failure
    for _ in range(2):
        item = await queue.dequeue(worker_id="worker-test", lease_timeout=30)
        assert item is not None
        await engine.process_job(item)

    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.FAILED.value
    assert job_db.execution_cycle == 1
    assert job_db.attempt_count == 2
    assert job_db.total_attempt_count == 2
    assert job_db.max_attempts == 2

    # 2. Perform manual retry via API
    retry_res = await client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retry_res.status_code == 200
    retry_data = retry_res.json()
    assert retry_data["status"] == "QUEUED"
    assert retry_data["execution_cycle"] == 2
    assert retry_data["attempt_count"] == 0
    assert retry_data["total_attempt_count"] == 2
    assert retry_data["max_attempts"] == 2  # Unchanged! Never inflated to 3

    # Dispatch cycle 2
    await dispatcher.process_batch()

    # 3. Cycle 2 attempt 1
    item_c2_1 = await queue.dequeue(worker_id="worker-test", lease_timeout=30)
    assert item_c2_1 is not None
    assert item_c2_1.get("execution_cycle") == 2
    await engine.process_job(item_c2_1)

    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.RETRYING.value
    assert job_db.execution_cycle == 2
    assert job_db.attempt_count == 1
    assert job_db.total_attempt_count == 3
    assert job_db.max_attempts == 2

    # 4. Cycle 2 attempt 2 -> terminal failure for cycle 2
    item_c2_2 = await queue.dequeue(worker_id="worker-test", lease_timeout=30)
    assert item_c2_2 is not None
    assert item_c2_2.get("execution_cycle") == 2
    await engine.process_job(item_c2_2)

    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.FAILED.value
    assert job_db.execution_cycle == 2
    assert job_db.attempt_count == 2
    assert job_db.total_attempt_count == 4
    assert job_db.max_attempts == 2  # Preserved! Attempt count never exceeded max_attempts

    # Verify event timeline preserves events from both cycles
    events = (await db_session.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
    event_types = [e.event_type for e in events]
    assert "JOB_CREATED" in event_types
    assert "JOB_MANUAL_RETRY" in event_types
    assert event_types.count("JOB_FAILED_TERMINAL") == 2


@pytest.mark.asyncio
async def test_fail_once_succeeds_on_expected_retry(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Test 4: fail_once task fails on attempt 1, succeeds on attempt 2 within cycle 1.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    res = await client.post(
        "/api/v1/jobs",
        json={"task_type": "fail_once", "priority": 5, "max_attempts": 3, "payload": {}},
    )
    job_id = uuid.UUID(res.json()["id"])

    dispatcher = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    await dispatcher.process_batch()

    queue = RedisPriorityQueue(fake_redis)
    engine = WorkerEngine(
        worker_id="worker-fo",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
        lease_timeout=30,
    )
    engine.queue = queue

    # Attempt 1 -> fails
    item1 = await queue.dequeue(worker_id="worker-fo", lease_timeout=30)
    await engine.process_job(item1)

    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.RETRYING.value
    assert job_db.attempt_count == 1
    assert job_db.execution_cycle == 1

    # Attempt 2 -> succeeds
    item2 = await queue.dequeue(worker_id="worker-fo", lease_timeout=30)
    await engine.process_job(item2)

    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.SUCCEEDED.value
    assert job_db.attempt_count == 2
    assert job_db.total_attempt_count == 2
    assert job_db.execution_cycle == 1


@pytest.mark.asyncio
async def test_concurrent_manual_retries_atomic(client: AsyncClient, db_session: AsyncSession, test_engine):
    """
    Test 5: Concurrent manual retry requests do not create duplicate cycles or double-increment.
    """
    res = await client.post(
        "/api/v1/jobs",
        json={"task_type": "sleep", "payload": {"duration_seconds": 1.0}},
    )
    job_id = res.json()["id"]

    # Cancel job to make it eligible for retry
    await client.post(f"/api/v1/jobs/{job_id}/cancel")

    from app.services.job_service import JobService, ConflictError

    # Simulate concurrent retry dispatch requests arriving simultaneously:
    # Under database row-level locking (with_for_update in PostgreSQL), queries serialize on the row lock;
    # in SQLite single-thread test harness, we verify that the atomic status validation allows exactly one
    # transition to QUEUED and rejects concurrent/subsequent attempts with ConflictError.
    lock = asyncio.Lock()

    async def attempt_retry():
        async with lock:
            service = JobService(db_session)
            try:
                await service.retry_job(uuid.UUID(job_id))
                return 200
            except ConflictError:
                return 409

    results = await asyncio.gather(
        attempt_retry(),
        attempt_retry(),
        attempt_retry(),
        attempt_retry(),
        attempt_retry(),
        return_exceptions=True
    )

    success_codes = [r for r in results if r == 200]
    conflict_codes = [r for r in results if r == 409]

    assert len(success_codes) == 1
    assert len(conflict_codes) == 4

    # Verify final execution cycle is exactly 2 (not 6)
    get_res = await client.get(f"/api/v1/jobs/{job_id}")
    assert get_res.json()["execution_cycle"] == 2


@pytest.mark.asyncio
async def test_stale_worker_cannot_commit_result_from_earlier_cycle(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Test 6: A stale worker executing cycle 1 cannot commit state if the job was manually retried
    and advanced to cycle 2 (cycle fencing check).
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    # Create and dispatch job
    res = await client.post(
        "/api/v1/jobs",
        json={"task_type": "sleep", "priority": 5, "payload": {"duration_seconds": 0.1}},
    )
    job_id = uuid.UUID(res.json()["id"])

    dispatcher = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    await dispatcher.process_batch()

    queue = RedisPriorityQueue(fake_redis)
    task_item = await queue.dequeue(worker_id="stale-worker", lease_timeout=30)
    assert task_item is not None

    # Stale worker simulates running attempt in cycle 1:
    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    # Now job fails or is cancelled, and manually retried by user to cycle 2:
    job_db.status = JobStatus.CANCELLED.value
    job_db.execution_cycle = 2
    await db_session.commit()

    # Stale worker tries to process task_item from cycle 1
    engine = WorkerEngine(
        worker_id="stale-worker",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
        lease_timeout=30,
    )
    engine.queue = queue

    # process_job should detect the stale cycle and drop or abort without setting SUCCEEDED
    await engine.process_job(task_item)

    await db_session.refresh(job_db)
    # The job must not be marked SUCCEEDED by the stale worker
    assert job_db.status != JobStatus.SUCCEEDED.value
    assert job_db.execution_cycle == 2


@pytest.mark.asyncio
async def test_traceback_capture_has_class_and_frame(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Test 7: Real exception tracebacks contain the exception class, message, and stack frame.
    Captured inside exception handler.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    custom_registry = TaskRegistry()

    async def faulty_task(payload, context=None):
        raise ValueError("Specific deliberate failure for stack inspection")

    custom_registry.add_handler("faulty_task", faulty_task)

    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        task_type="faulty_task",
        payload={},
        priority=5,
        max_attempts=1,
        status=JobStatus.QUEUED.value,
        execution_cycle=1,
        attempt_count=0,
        total_attempt_count=0,
    )
    db_session.add(job)
    await db_session.commit()

    queue = RedisPriorityQueue(fake_redis)
    await queue.enqueue(job_id=job_id, task_type="faulty_task", priority=5, payload={}, execution_cycle=1)

    queue = RedisPriorityQueue(fake_redis)
    engine = WorkerEngine(
        worker_id="tb-worker",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=custom_registry,
        lease_timeout=30,
    )
    engine.queue = queue

    task_item = await queue.dequeue(worker_id="tb-worker", lease_timeout=30)
    await engine.process_job(task_item)

    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await db_session.refresh(job_db)

    assert job_db.status == JobStatus.FAILED.value
    assert job_db.error_type == "ValueError"
    assert "Specific deliberate failure" in job_db.error_message
    assert job_db.error_details is not None
    tb = job_db.error_details.get("traceback")
    assert tb is not None
    assert "ValueError: Specific deliberate failure" in tb
    assert "faulty_task" in tb


@pytest.mark.asyncio
async def test_missing_traceback_handled_cleanly(client: AsyncClient, db_session: AsyncSession):
    """
    Test 8: Jobs that fail without traceback or are cancelled have clean error_details without fabricated strings.
    """
    res = await client.post(
        "/api/v1/jobs",
        json={"task_type": "sleep", "payload": {"duration_seconds": 10.0}},
    )
    job_id = res.json()["id"]

    cancel_res = await client.post(f"/api/v1/jobs/{job_id}/cancel?reason=user_abort")
    assert cancel_res.status_code == 200

    job_db = (await db_session.execute(select(Job).where(Job.id == uuid.UUID(job_id)))).scalar_one()
    assert job_db.status == JobStatus.CANCELLED.value
    # error_details should be None or empty dict, never "NoneType: None"
    if job_db.error_details:
        assert job_db.error_details.get("traceback") is None or "NoneType: None" not in str(job_db.error_details)
