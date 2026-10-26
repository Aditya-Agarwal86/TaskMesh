import pytest
import uuid
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import JobStatus
from app.models.job import Job
from app.models.state_machine import validate_transition, InvalidStateTransitionError
from app.queue.priority_queue import RedisPriorityQueue
from app.workers.dispatcher import OutboxDispatcher
from app.workers.engine import WorkerEngine
from app.workers.task_registry import TaskRegistry, default_task_registry, PermanentTaskError


# ============================================================================
# 1. IDEMPOTENCY: Equivalence & Concurrent Collision Tests
# ============================================================================

@pytest.mark.asyncio
async def test_idempotency_same_key_same_payload_returns_original(client: AsyncClient):
    """Repeated submissions with the same key and equivalent request return original job."""
    key = f"idem-valid-{uuid.uuid4()}"
    payload = {"task_type": "pdf_render", "payload": {"document_id": "doc-100"}, "idempotency_key": key}

    res1 = await client.post("/api/v1/jobs", json=payload)
    assert res1.status_code == 202
    job1 = res1.json()

    res2 = await client.post("/api/v1/jobs", json=payload)
    assert res2.status_code == 202
    job2 = res2.json()

    assert job1["id"] == job2["id"]


@pytest.mark.asyncio
async def test_idempotency_same_key_different_payload_rejected(client: AsyncClient):
    """Reject reuse of the same key with a materially different request."""
    key = f"idem-diff-{uuid.uuid4()}"

    res1 = await client.post(
        "/api/v1/jobs",
        json={"task_type": "pdf_render", "payload": {"document_id": "doc-1"}, "idempotency_key": key},
    )
    assert res1.status_code == 202

    # Attempt to reuse with different payload
    res2 = await client.post(
        "/api/v1/jobs",
        json={"task_type": "pdf_render", "payload": {"document_id": "doc-DIFFERENT"}, "idempotency_key": key},
    )
    assert res2.status_code == 409
    assert "materially different" in res2.json()["detail"]["message"]

    # Attempt to reuse with different task_type
    res3 = await client.post(
        "/api/v1/jobs",
        json={"task_type": "email_notification", "payload": {"document_id": "doc-1"}, "idempotency_key": key},
    )
    assert res3.status_code == 409



# ============================================================================
# 2. RETRIES & PERMANENT ERRORS
# ============================================================================

@pytest.mark.asyncio
async def test_permanent_error_no_retries(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """
    Tasks that raise PermanentTaskError (such as schema validation failures)
    must fail immediately without consuming retry attempts or backoff delays.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    from app.workers.task_registry import default_task_registry

    @default_task_registry.register("invalid_data_task")
    async def handle_invalid_data(payload):
        raise PermanentTaskError("Fatal data format corruption, cannot retry.")

    res = await client.post(
        "/api/v1/jobs",
        json={"task_type": "invalid_data_task", "max_attempts": 5, "payload": {}},
    )
    assert res.status_code == 202
    job_id = uuid.UUID(res.json()["id"])

    dispatcher = OutboxDispatcher(redis_client=fake_redis, session_factory=session_factory)
    await dispatcher.process_batch()

    queue = RedisPriorityQueue(fake_redis)
    task_item = await queue.dequeue(worker_id="worker-perm-test")
    assert task_item is not None

    engine = WorkerEngine(
        worker_id="worker-perm-test",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
    )
    engine.queue = queue
    await engine.process_job(task_item)

    # Must transition directly to FAILED on attempt 1 without entering RETRYING
    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await db_session.refresh(job_db)
    assert job_db.status == JobStatus.FAILED.value
    assert job_db.attempt_count == 1
    assert "Fatal data format corruption" in job_db.error_message


# ============================================================================
# 3. WORKER LEASES, FENCING TOKENS & ZOMBIE WORKER PREVENTION
# ============================================================================

@pytest.mark.asyncio
async def test_stale_worker_fencing_prevents_overwrite(db_session: AsyncSession, fake_redis):
    """
    If a worker loses its Redis lease (e.g. paused/zombie worker whose lease timed out and was stolen),
    its fencing verification must prevent it from committing completion results to PostgreSQL.
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
        task_type="email_notification",
        payload={"to": "user@example.com"},
        priority=5,
        status=JobStatus.QUEUED.value,
        attempt_count=0,
        max_attempts=3,
    )
    db_session.add(job)
    await db_session.commit()

    queue = RedisPriorityQueue(fake_redis)
    await queue.enqueue(job_id=job_id, task_type="email_notification", priority=5)

    # Worker A claims the job with a short 1-second lease
    task_item = await queue.dequeue(worker_id="worker-zombie", lease_timeout=1)
    assert task_item is not None

    engine_zombie = WorkerEngine(
        worker_id="worker-zombie",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
        lease_timeout=1,
    )
    engine_zombie.queue = queue

    # Simulate lease expiry & theft by another worker:
    # Delete Worker A's lease key and assign to Worker B
    await fake_redis.set(f"taskmesh:lease:{job_id}", "worker-new-owner", ex=30)

    # Worker A finishes execution and attempts to commit
    # Fencing check should detect Worker A does not hold the lease and refuse commit
    await engine_zombie.process_job(task_item)

    # Verify job status in DB is NOT marked SUCCEEDED by worker-zombie
    job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await db_session.refresh(job_db)
    # Status remains RUNNING or previous state; Worker Zombie's success event was never persisted
    assert job_db.status != JobStatus.SUCCEEDED.value


# ============================================================================
# 4. EXPIRED LEASE RECOVERY (Crash Safety)
# ============================================================================

@pytest.mark.asyncio
async def test_expired_lease_recovery(db_session: AsyncSession, fake_redis):
    """
    Verifies that abandoned/stale jobs whose lease expired in PostgreSQL and Redis
    are detected by recover_stale_jobs and automatically rescheduled for retry.
    """
    from contextlib import asynccontextmanager
    import datetime

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    job_id = uuid.uuid4()
    past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=120)

    # Job is stuck in RUNNING with lease_expires_at in the past, worker crashed
    job = Job(
        id=job_id,
        task_type="email_notification",
        payload={"to": "recovery@example.com"},
        priority=5,
        status=JobStatus.RUNNING.value,
        attempt_count=1,
        max_attempts=3,
        worker_id="crashed-worker-99",
        lease_expires_at=past_time,
    )
    db_session.add(job)
    await db_session.commit()

    engine = WorkerEngine(
        worker_id="supervisor-worker",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
    )
    engine.queue = RedisPriorityQueue(fake_redis)

    recovered = await engine.recover_stale_jobs()
    assert recovered == 1

    # Verify job transitioned to RETRYING with error message noting lease timeout
    await db_session.refresh(job)
    assert job.status == JobStatus.RETRYING.value
    assert "Lease expired" in job.error_message
    assert job.next_retry_at is not None


# ============================================================================
# 5. STATE TRANSITIONS ENFORCEMENT
# ============================================================================

def test_state_machine_transition_enforcement():
    """Validates that legal transitions pass and illegal transitions raise InvalidStateTransitionError."""
    # Legal transitions
    validate_transition(JobStatus.QUEUED, JobStatus.RUNNING)
    validate_transition(JobStatus.RUNNING, JobStatus.SUCCEEDED)
    validate_transition(JobStatus.RUNNING, JobStatus.FAILED)
    validate_transition(JobStatus.RUNNING, JobStatus.RETRYING)
    validate_transition(JobStatus.RETRYING, JobStatus.RUNNING)
    validate_transition(JobStatus.FAILED, JobStatus.QUEUED)  # Manual retry
    validate_transition(JobStatus.CANCELLED, JobStatus.QUEUED)  # Manual retry

    # Illegal transitions
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)

    with pytest.raises(InvalidStateTransitionError):
        validate_transition(JobStatus.SUCCEEDED, JobStatus.QUEUED)

    with pytest.raises(InvalidStateTransitionError):
        validate_transition(JobStatus.QUEUED, JobStatus.SUCCEEDED)


# ============================================================================
# 6. OBSERVABILITY & METRICS ENDPOINT
# ============================================================================

@pytest.mark.asyncio
async def test_health_metrics_endpoint(client: AsyncClient, db_session: AsyncSession):
    """Verifies that /health/metrics exposes queue depth, worker heartbeats, and status distribution."""
    # Create sample job
    await client.post("/api/v1/jobs", json={"task_type": "email_notification"})

    res = await client.get("/health/metrics")
    assert res.status_code == 200
    data = res.json()
    assert "queue_depth" in data
    assert "pending" in data["queue_depth"]
    assert "processing" in data["queue_depth"]
    assert "job_counts_by_status" in data
    assert data["job_counts_by_status"]["QUEUED"] >= 1
    assert data["service_status"] == "healthy"
