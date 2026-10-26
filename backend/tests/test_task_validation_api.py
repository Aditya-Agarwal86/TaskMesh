import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, OutboxEvent
from app.workers.task_registry import default_task_registry


@pytest.mark.asyncio
async def test_all_registered_task_types_accepted_with_valid_payload(client: AsyncClient, db_session: AsyncSession):
    """
    Verifies that every single registered task type in the authoritative registry is
    accepted at the API boundary, returning HTTP 202, and creates a Job and OutboxEvent.
    """
    registered_types = default_task_registry.get_supported_task_types()
    assert len(registered_types) >= 7

    # Sample valid payloads for each registered task
    valid_payloads = {
        "sleep": {"duration_seconds": 0.5, "checkpoint_interval": 0.25},
        "generate_report": {
            "report_name": "Test Report",
            "dataset": [{"metric": "cpu", "value": 15}, {"metric": "mem", "value": 30}],
            "format": "summary",
        },
        "fail_once": {"attempt_count": 2},
        "always_fail": {"reason": "Test failure injection"},
        "email_notification": {"to": "audit@example.com", "template": "welcome"},
        "invoice_generation": {"order_id": 5012},
        "pdf_render": {"document_id": "doc-999"},
        "data_sync": {"records_count": 50},
    }

    for task_type in registered_types:
        payload = valid_payloads.get(task_type, {})
        res = await client.post(
            "/api/v1/jobs",
            json={"task_type": task_type, "payload": payload, "priority": 5},
        )
        assert res.status_code == 202, f"Expected 202 for task_type '{task_type}', got {res.status_code}: {res.text}"
        data = res.json()
        assert data["task_type"] == task_type
        assert data["status"] == "QUEUED"

        # Verify job and outbox event were created in DB
        job_id = uuid.UUID(data["id"])
        job_db = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        assert job_db is not None
        assert job_db.task_type == task_type

        outbox_db = (await db_session.execute(select(OutboxEvent).where(OutboxEvent.job_id == job_id))).scalar_one_or_none()
        assert outbox_db is not None


@pytest.mark.asyncio
async def test_unknown_task_types_rejected_with_422_and_supported_types(client: AsyncClient, db_session: AsyncSession):
    """
    Verifies that unknown task types (e.g. 'Lunch', 'm,k,k', 'exec_shell', 'unknown_xyz')
    are rejected with HTTP 422 and return the list of supported types in the response.
    Crucially: Neither a Job nor an OutboxEvent must be persisted in the database.
    """
    invalid_task_types = ["Lunch", "m,k,k", "exec_shell", "unknown_worker_task", "sudo_rm"]

    jobs_count_before = (await db_session.execute(select(func.count(Job.id)))).scalar()
    outbox_count_before = (await db_session.execute(select(func.count(OutboxEvent.id)))).scalar()

    for task_type in invalid_task_types:
        res = await client.post(
            "/api/v1/jobs",
            json={"task_type": task_type, "priority": 5, "payload": {"data": "foo"}},
        )
        assert res.status_code == 422
        body = res.json()
        assert "detail" in body
        detail = body["detail"]
        assert detail["code"] == "UNSUPPORTED_TASK_TYPE_OR_INVALID_PAYLOAD"
        assert f"Unsupported task type '{task_type}'" in detail["message"]
        assert "supported_types" in detail
        assert isinstance(detail["supported_types"], list)
        assert "sleep" in detail["supported_types"]
        assert "generate_report" in detail["supported_types"]

    # Verify no database records were created for rejected submissions
    jobs_count_after = (await db_session.execute(select(func.count(Job.id)))).scalar()
    outbox_count_after = (await db_session.execute(select(func.count(OutboxEvent.id)))).scalar()

    assert jobs_count_after == jobs_count_before
    assert outbox_count_after == outbox_count_before


@pytest.mark.asyncio
async def test_malformed_payload_rejected_with_422_and_no_db_records(client: AsyncClient, db_session: AsyncSession):
    """
    Verifies that supported task types with invalid or malformed payloads
    (violating Pydantic schemas) are rejected with HTTP 422 without writing to DB.
    """
    jobs_count_before = (await db_session.execute(select(func.count(Job.id)))).scalar()
    outbox_count_before = (await db_session.execute(select(func.count(OutboxEvent.id)))).scalar()

    # 1. sleep with negative duration
    res1 = await client.post(
        "/api/v1/jobs",
        json={"task_type": "sleep", "payload": {"duration_seconds": -5.0}},
    )
    assert res1.status_code == 422
    assert "payload validation failed" in res1.json()["detail"]["message"]

    # 2. invoice_generation with non-integer or negative order_id
    res2 = await client.post(
        "/api/v1/jobs",
        json={"task_type": "invoice_generation", "payload": {"order_id": -99}},
    )
    assert res2.status_code == 422

    # 3. email_notification with empty recipient
    res3 = await client.post(
        "/api/v1/jobs",
        json={"task_type": "email_notification", "payload": {"to": "x"}},
    )
    assert res3.status_code == 422

    # Verify no records written
    jobs_count_after = (await db_session.execute(select(func.count(Job.id)))).scalar()
    outbox_count_after = (await db_session.execute(select(func.count(OutboxEvent.id)))).scalar()
    assert jobs_count_after == jobs_count_before
    assert outbox_count_after == outbox_count_before


@pytest.mark.asyncio
async def test_worker_side_defense_in_depth(db_session: AsyncSession, fake_redis):
    """
    Verifies that worker engine rejects unregistered task types as PermanentTaskError
    without retrying, acting as defense in depth if an invalid job ever bypasses the API.
    """
    from app.workers.engine import WorkerEngine
    from app.models.enums import JobStatus
    from app.queue.priority_queue import RedisPriorityQueue
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_session():
        yield db_session

    def session_factory():
        return mock_session()

    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        task_type="bypassed_unregistered_task",
        payload={},
        priority=5,
        max_attempts=3,
        status=JobStatus.QUEUED.value,
        attempt_count=0,
    )
    db_session.add(job)
    await db_session.commit()

    queue = RedisPriorityQueue(fake_redis)
    await queue.enqueue(job_id=job_id, task_type="bypassed_unregistered_task", priority=5)

    engine = WorkerEngine(
        worker_id="worker-did",
        redis_client=fake_redis,
        session_factory=session_factory,
        registry=default_task_registry,
        lease_timeout=30,
    )
    engine.queue = queue

    task_item = await queue.dequeue(worker_id="worker-did", lease_timeout=30)
    assert task_item is not None

    await engine.process_job(task_item)

    await db_session.refresh(job)
    # Must immediately transition to FAILED without retrying
    assert job.status == JobStatus.FAILED.value
    assert job.error_type == "PermanentTaskError"
    assert "No task handler registered" in job.error_message
