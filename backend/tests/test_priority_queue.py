import pytest
from app.queue.priority_queue import RedisPriorityQueue


@pytest.mark.asyncio
async def test_priority_ordering(fake_redis):
    queue = RedisPriorityQueue(fake_redis)

    # Enqueue low priority job
    await queue.enqueue(job_id="job-low", task_type="email", priority=1)
    # Enqueue critical priority job
    await queue.enqueue(job_id="job-crit", task_type="payment", priority=10)
    # Enqueue normal priority job
    await queue.enqueue(job_id="job-normal", task_type="export", priority=5)

    # First dequeued must be the critical priority job
    item1 = await queue.dequeue(worker_id="w1", lease_timeout=30)
    assert item1 is not None
    assert item1["job_id"] == "job-crit"
    assert item1["task_type"] == "payment"
    assert item1["worker_id"] == "w1"

    # Second dequeued must be normal
    item2 = await queue.dequeue(worker_id="w2", lease_timeout=30)
    assert item2 is not None
    assert item2["job_id"] == "job-normal"

    # Third dequeued must be low
    item3 = await queue.dequeue(worker_id="w3", lease_timeout=30)
    assert item3 is not None
    assert item3["job_id"] == "job-low"

    # Queue should now be empty
    item4 = await queue.dequeue(worker_id="w1")
    assert item4 is None


@pytest.mark.asyncio
async def test_lease_heartbeat_renewal(fake_redis):
    queue = RedisPriorityQueue(fake_redis)
    job_id = "job-lease-test"

    await queue.enqueue(job_id=job_id, task_type="sync", priority=5)
    item = await queue.dequeue(worker_id="worker-abc", lease_timeout=10)
    assert item is not None
    assert item["job_id"] == job_id

    # Renew lease by same worker succeeds
    renewed = await queue.renew_lease(job_id=job_id, worker_id="worker-abc", ttl=20)
    assert renewed is True

    # Renew lease by different worker fails
    unauthorized_renew = await queue.renew_lease(job_id=job_id, worker_id="worker-imposter", ttl=20)
    assert unauthorized_renew is False


@pytest.mark.asyncio
async def test_completion_acknowledgement(fake_redis):
    queue = RedisPriorityQueue(fake_redis)
    job_id = "job-ack-test"

    await queue.enqueue(job_id=job_id, task_type="render", priority=5)
    await queue.dequeue(worker_id="worker-1", lease_timeout=30)

    metrics_before = await queue.get_queue_metrics()
    assert metrics_before["processing"] == 1

    await queue.acknowledge_completion(job_id)

    metrics_after = await queue.get_queue_metrics()
    assert metrics_after["processing"] == 0
    assert metrics_after["pending"] == 0
