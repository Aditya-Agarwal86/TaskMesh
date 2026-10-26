import pytest
from app.services.broadcaster import broadcaster
from app.services.event_notifier import notify_event


from unittest.mock import AsyncMock, MagicMock
from app.api.v1.events import event_stream

@pytest.mark.asyncio
async def test_sse_stream_connection_and_heartbeat():
    """Validates SSE stream connection event, event delivery, and unregister on disconnect."""
    req = MagicMock()
    req.is_disconnected = AsyncMock(side_effect=[False, False, False, True])

    initial_subs = broadcaster.active_connections_count
    resp = await event_stream(req)

    assert resp.status_code == 200
    assert resp.media_type == "text/event-stream"
    assert broadcaster.active_connections_count == initial_subs + 1

    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk)
        # Deliver an event while connected
        if len(chunks) == 1:
            await notify_event(
                "job_update",
                {"job_id": "test-stream-uuid", "status": "RUNNING", "event_type": "JOB_STARTED"},
            )

    # Verify initial connected event and subsequent job_update event was yielded
    assert any("event: connected" in c for c in chunks)
    assert any("event: job_update" in c for c in chunks)
    assert any("test-stream-uuid" in c for c in chunks)

    # After iterator terminates, client is unregistered
    assert broadcaster.active_connections_count == initial_subs



@pytest.mark.asyncio
async def test_operational_metrics_contract(client):
    """Validates that /health/metrics calculates and returns all operational telemetry fields."""
    res = await client.get("/health/metrics")
    assert res.status_code == 200
    data = res.json()

    assert "queue_depth" in data
    assert "pending" in data["queue_depth"]
    assert "processing" in data["queue_depth"]
    assert "dlq" in data["queue_depth"]

    assert "job_counts_by_status" in data
    assert "completed_jobs" in data
    assert "failed_jobs" in data
    assert "total_retries" in data
    assert "active_workers_count" in data
    assert "workers" in data
    assert "execution_duration" in data

    dur = data["execution_duration"]
    assert "avg_seconds" in dur
    assert "min_seconds" in dur
    assert "max_seconds" in dur
    assert "sample_count" in dur

