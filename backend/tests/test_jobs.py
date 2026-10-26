import pytest
import uuid
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.job import Job, JobEvent, OutboxEvent
from app.models.enums import JobStatus, OutboxStatus


@pytest.mark.asyncio
async def test_health_live(client: AsyncClient):
    response = await client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "taskmesh-api"


@pytest.mark.asyncio
async def test_health_ready(client: AsyncClient):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"


@pytest.mark.asyncio
async def test_create_job_atomic_persistence(client: AsyncClient, db_session: AsyncSession):
    """Verifies that job creation writes Job, JobEvent, and OutboxEvent atomically."""
    payload = {
        "task_type": "email_notification",
        "payload": {"to": "user@example.com", "template": "welcome"},
        "priority": 8,
        "max_attempts": 5,
        "idempotency_key": "user-reg-1234",
    }
    response = await client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert data["task_type"] == "email_notification"
    assert data["status"] == "QUEUED"
    assert data["priority"] == 8
    assert data["max_attempts"] == 5
    assert data["idempotency_key"] == "user-reg-1234"
    assert "id" in data
    job_id = uuid.UUID(data["id"])

    # Verify Job record
    job = (await db_session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job.status == JobStatus.QUEUED.value

    # Verify initial JobEvent
    events = (await db_session.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "JOB_CREATED"

    # Verify OutboxEvent
    outbox = (await db_session.execute(select(OutboxEvent).where(OutboxEvent.job_id == job_id))).scalars().all()
    assert len(outbox) == 1
    assert outbox[0].status == OutboxStatus.PENDING.value
    assert outbox[0].event_type == "JOB_QUEUED"
    assert outbox[0].payload["job_id"] == str(job_id)


@pytest.mark.asyncio
async def test_idempotency_key_deduplication(client: AsyncClient):
    """Submitting the same idempotency key returns the existing job without duplicates."""
    key = f"idemp-{uuid.uuid4()}"
    payload = {
        "task_type": "invoice_generation",
        "payload": {"order_id": 999},
        "idempotency_key": key,
    }

    first_res = await client.post("/api/v1/jobs", json=payload)
    assert first_res.status_code == 202
    first_job = first_res.json()

    # Second submission with same key
    second_res = await client.post("/api/v1/jobs", json=payload)
    assert second_res.status_code == 202
    second_job = second_res.json()

    assert first_job["id"] == second_job["id"]


@pytest.mark.asyncio
async def test_input_validation(client: AsyncClient):
    """Rejects invalid priorities, empty task types, and bad types."""
    # Priority too high (> 10)
    res = await client.post("/api/v1/jobs", json={"task_type": "test", "priority": 99})
    assert res.status_code == 422
    data = res.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"

    # Empty task_type
    res = await client.post("/api/v1/jobs", json={"task_type": "", "priority": 5})
    assert res.status_code == 422

    # Unsupported task type (e.g. "Lunch", "m,k,k")
    res_lunch = await client.post("/api/v1/jobs", json={"task_type": "Lunch", "priority": 5})
    assert res_lunch.status_code == 422
    assert "Unsupported task type 'Lunch'" in res_lunch.json()["detail"]["message"]

    res_gibberish = await client.post("/api/v1/jobs", json={"task_type": "m,k,k", "priority": 5})
    assert res_gibberish.status_code == 422

    # Supported task type but invalid payload schema
    res_bad_payload = await client.post(
        "/api/v1/jobs",
        json={"task_type": "sleep", "payload": {"duration_seconds": -50.0}},
    )
    assert res_bad_payload.status_code == 422
    assert "payload validation failed" in res_bad_payload.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_get_job_details_and_not_found(client: AsyncClient):
    # Non-existent job
    random_id = uuid.uuid4()
    res = await client.get(f"/api/v1/jobs/{random_id}")
    assert res.status_code == 404

    # Create job and retrieve
    create_res = await client.post("/api/v1/jobs", json={"task_type": "pdf_render"})
    assert create_res.status_code == 202
    job_id = create_res.json()["id"]

    get_res = await client.get(f"/api/v1/jobs/{job_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == job_id
    assert get_res.json()["task_type"] == "pdf_render"


@pytest.mark.asyncio
async def test_list_and_filter_jobs(client: AsyncClient):
    # Create two jobs with supported task types and priorities
    await client.post("/api/v1/jobs", json={"task_type": "sleep", "payload": {"duration_seconds": 1.0}, "priority": 2})
    await client.post("/api/v1/jobs", json={"task_type": "pdf_render", "payload": {"document_id": "doc-9"}, "priority": 9})

    # Filter by task_type
    res_a = await client.get("/api/v1/jobs?task_type=sleep")
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert data_a["total"] >= 1
    assert all(item["task_type"] == "sleep" for item in data_a["items"])

    # Filter by priority
    res_p = await client.get("/api/v1/jobs?priority=9")
    assert res_p.status_code == 200
    data_p = res_p.json()
    assert any(item["priority"] == 9 for item in data_p["items"])


@pytest.mark.asyncio
async def test_job_events_audit_trail(client: AsyncClient):
    create_res = await client.post("/api/v1/jobs", json={"task_type": "sleep", "payload": {"duration_seconds": 1.0}})
    job_id = create_res.json()["id"]

    events_res = await client.get(f"/api/v1/jobs/{job_id}/events")
    assert events_res.status_code == 200
    events = events_res.json()
    assert len(events) >= 1
    assert events[0]["event_type"] == "JOB_CREATED"
    assert events[0]["job_id"] == job_id


@pytest.mark.asyncio
async def test_cancel_and_retry_flow(client: AsyncClient):
    create_res = await client.post("/api/v1/jobs", json={"task_type": "sleep", "payload": {"duration_seconds": 5.0}})
    job_id = create_res.json()["id"]

    # Cancel job
    cancel_res = await client.post(f"/api/v1/jobs/{job_id}/cancel?reason=user_requested")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"

    # Cannot cancel again (terminal state)
    cancel_again = await client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancel_again.status_code == 409

    # Retry cancelled job
    retry_res = await client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "QUEUED"

    # Check updated events timeline
    events_res = await client.get(f"/api/v1/jobs/{job_id}/events")
    types = [e["event_type"] for e in events_res.json()]
    assert "JOB_CREATED" in types
    assert "JOB_CANCELLED" in types
    assert "JOB_MANUAL_RETRY" in types
