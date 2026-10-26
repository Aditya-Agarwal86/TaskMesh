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
from app.workers.task_registry import TaskRegistry, default_task_registry


@pytest.mark.asyncio
async def test_duplicate_submission_idempotency_flow(client: AsyncClient):
    """
    Tests that rapid repeated requests with the same idempotency key are safely deduplicated,
    returning the original job ID and status without conflicting or creating duplicates.
    """
    key = f"idem-suite-{uuid.uuid4()}"
    payload = {
        "task_type": "sleep",
        "payload": {"duration_seconds": 0.1},
        "idempotency_key": key,
    }

    res1 = await client.post("/api/v1/jobs", json=payload)
    assert res1.status_code == 202
    job1 = res1.json()

    res2 = await client.post("/api/v1/jobs", json=payload)
    assert res2.status_code == 202
    job2 = res2.json()

    assert job1["id"] == job2["id"]
    assert job1["idempotency_key"] == key


@pytest.mark.asyncio
async def test_outbox_replay_and_duplicate_publishing(db_session: AsyncSession, fake_redis):
    """
    If dispatcher publishes to Redis, crashes, and restarts:
    Republishing the same outbox event must not enqueue duplicate entries in Redis,
    and the outbox event must cleanly transition to DISPATCHED.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        task_type="generate_report",
        payload={"quarter": "Q3"},
        priority=8,
        status=JobStatus.QUEUED.value,
        attempt_count=0,
        max_attempts=3,
    )
    outbox = OutboxEvent(
        id=uuid.uuid4(),
        job_id=job_id,
        event_type="JOB_QUEUED",
        payload={"job_id": str(job_id), "task_type": "generate_report", "priority": 8, "payload": {"quarter": "Q3"}},
        status=OutboxStatus.PENDING.value,
    )
    db_session.add(job)
    db_session.add(outbox)
    await db_session.commit()

    dispatcher1 = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    processed1 = await dispatcher1.process_batch()
    assert processed1 == 1

    # Check Redis queue depth is 1
    queue = RedisPriorityQueue(fake_redis)
    metrics1 = await queue.get_queue_metrics()
    assert metrics1["pending"] == 1

    # Simulate crash before PostgreSQL update was committed: reset outbox back to PENDING
    outbox.status = OutboxStatus.PENDING.value
    await db_session.commit()

    # Restart dispatcher and process again
    dispatcher2 = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    processed2 = await dispatcher2.process_batch()
    assert processed2 == 1

    # Queue depth must still be 1 (idempotent enqueue)
    metrics2 = await queue.get_queue_metrics()
    assert metrics2["pending"] == 1

    await db_session.refresh(outbox)
    assert outbox.status == OutboxStatus.DISPATCHED.value


@pytest.mark.asyncio
async def test_cancellation_race_condition_during_execution(db_session: AsyncSession, fake_redis):
    """
    If a job is CANCELLED via API while worker is executing,
    the worker's cancellation checkpoint should detect it and abort without marking SUCCEEDED.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    registry = TaskRegistry()

    @registry.register("cancellable_step_task")
    async def handle_cancellable(payload, checkpoint=None):
        for _ in range(5):
            await asyncio.sleep(0.05)
            if checkpoint and await checkpoint():
                return None
        return {"completed": True}

    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        task_type="cancellable_step_task",
        payload={},
        priority=5,
        status=JobStatus.RUNNING.value,
        attempt_count=1,
        max_attempts=3,
        worker_id="test-worker-cancel",
    )
    db_session.add(job)
    await db_session.commit()

    queue = RedisPriorityQueue(fake_redis)
    await queue.enqueue(job_id=job_id, task_type="cancellable_step_task", priority=5)
    task_item = await queue.dequeue(worker_id="test-worker-cancel", lease_timeout=30)
    assert task_item is not None

    engine = WorkerEngine(
        worker_id="test-worker-cancel",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=registry,
    )
    engine.queue = queue

    # Simulate cancellation in DB mid-flight
    job.status = JobStatus.CANCELLED.value
    await db_session.commit()

    await engine.process_job(task_item)

    await db_session.refresh(job)
    assert job.status == JobStatus.CANCELLED.value
    # Ensure job was acknowledged and cleaned up from Redis processing set
    metrics = await queue.get_queue_metrics()
    assert metrics["processing"] == 0


@pytest.mark.asyncio
async def test_worker_termination_and_lease_expiry_handoff(db_session: AsyncSession, fake_redis):
    """
    Tests graceful handoff when Worker A claims a job and terminates/hangs.
    After lease expiry, the supervisor recovers it, and Worker B successfully claims and finishes it.
    """
    from contextlib import asynccontextmanager
    import datetime

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    job_id = uuid.uuid4()
    past_lease = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5)

    job = Job(
        id=job_id,
        task_type="generate_report",
        payload={"report_name": "monthly_sales", "region": "EU"},
        priority=8,
        status=JobStatus.RUNNING.value,
        attempt_count=1,
        max_attempts=3,
        worker_id="worker-terminated-1",
        lease_expires_at=past_lease,
    )
    db_session.add(job)
    await db_session.commit()

    queue = RedisPriorityQueue(fake_redis)

    # Supervisor recovers stale job
    engine_supervisor = WorkerEngine(
        worker_id="worker-supervisor",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
    )
    engine_supervisor.queue = queue

    recovered_count = await engine_supervisor.recover_stale_jobs()
    assert recovered_count == 1

    await db_session.refresh(job)
    assert job.status == JobStatus.RETRYING.value

    # Fast-forward queue item ready time to now so dequeue can claim it
    await queue.enqueue(
        job_id=job.id,
        task_type=job.task_type,
        priority=job.priority,
        payload=job.payload,
        scheduled_at=None,
    )

    # Worker B claims and processes
    item_b = await queue.dequeue(worker_id="worker-replacement-2")
    assert item_b is not None
    assert str(item_b["job_id"]) == str(job_id)

    engine_b = WorkerEngine(
        worker_id="worker-replacement-2",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
    )
    engine_b.queue = queue
    await engine_b.process_job(item_b)

    await db_session.refresh(job)
    assert job.status == JobStatus.SUCCEEDED.value
    assert job.worker_id == "worker-replacement-2"


@pytest.mark.asyncio
async def test_job_creation_persists_initial_job_created_event(client: AsyncClient, db_session: AsyncSession):
    """
    Requirements 1 & 2: Every newly created job must have a durable creation event
    (JOB_CREATED) stored atomically in PostgreSQL.
    """
    res = await client.post(
        "/api/v1/jobs",
        json={"task_type": "sleep", "payload": {"duration_seconds": 0.1}, "priority": 8},
    )
    assert res.status_code == 202
    job_id = uuid.UUID(res.json()["id"])

    # Query events directly from DB
    events = (await db_session.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
    assert len(events) >= 1
    assert events[0].event_type == "JOB_CREATED"
    assert events[0].metadata_payload.get("priority") == 8
    assert events[0].metadata_payload.get("task_type") == "sleep"

    # Also verify the API events endpoint exposes the creation event
    api_events_res = await client.get(f"/api/v1/jobs/{job_id}/events")
    assert api_events_res.status_code == 200
    api_events = api_events_res.json()
    assert len(api_events) >= 1
    assert api_events[0]["event_type"] == "JOB_CREATED"


@pytest.mark.asyncio
async def test_terminal_jobs_cannot_be_re_enqueued_or_double_transitioned(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Requirement 8: Succeeded terminal jobs cannot be re-enqueued through ordinary retry paths.
    Attempting to call /retry on a SUCCEEDED job returns 409 Conflict.
    """
    res = await client.post(
        "/api/v1/jobs",
        json={"task_type": "sleep", "payload": {"duration_seconds": 0.1}},
    )
    job_id = res.json()["id"]

    # Mark job as SUCCEEDED in database
    job_db = (await db_session.execute(select(Job).where(Job.id == uuid.UUID(job_id)))).scalar_one()
    job_db.status = JobStatus.SUCCEEDED.value
    await db_session.commit()

    # Attempt to retry the SUCCEEDED job
    retry_res = await client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retry_res.status_code == 409
    body = retry_res.json()
    assert "cannot be manually retried" in body["detail"]["message"].lower() or "INVALID_STATE_FOR_RETRY" in str(body)

    # Verify status remains SUCCEEDED
    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_cancellation_before_execution_and_around_retry(client: AsyncClient, db_session: AsyncSession):
    """
    Requirement 6: Test cancellation before execution (while QUEUED) and around retry scheduling (while RETRYING).
    """
    # 1. Cancel while QUEUED
    res1 = await client.post(
        "/api/v1/jobs",
        json={"task_type": "sleep", "payload": {"duration_seconds": 1.0}},
    )
    job_id1 = res1.json()["id"]

    cancel_res1 = await client.post(f"/api/v1/jobs/{job_id1}/cancel?reason=cancelled_while_queued")
    assert cancel_res1.status_code == 200
    assert cancel_res1.json()["status"] == "CANCELLED"

    # 2. Cancel while RETRYING
    res2 = await client.post(
        "/api/v1/jobs",
        json={"task_type": "fail_once", "payload": {}},
    )
    job_id2 = res2.json()["id"]

    job_db2 = (await db_session.execute(select(Job).where(Job.id == uuid.UUID(job_id2)))).scalar_one()
    job_db2.status = JobStatus.RETRYING.value
    await db_session.commit()

    cancel_res2 = await client.post(f"/api/v1/jobs/{job_id2}/cancel?reason=cancelled_while_retrying")
    assert cancel_res2.status_code == 200
    assert cancel_res2.json()["status"] == "CANCELLED"

