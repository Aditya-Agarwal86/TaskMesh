import json
import time
import uuid
from typing import Any, Dict, List, Optional
import redis.asyncio as aioredis
from app.core.logging import get_logger

logger = get_logger("taskmesh.priority_queue")

# Redis Key Constants
DEFAULT_QUEUE_KEY = "taskmesh:queue:pending"
DEFAULT_PROCESSING_KEY = "taskmesh:queue:processing"
DEFAULT_DEAD_LETTER_KEY = "taskmesh:queue:dlq"
LEASE_PREFIX = "taskmesh:lease:"


class RedisPriorityQueue:
    """
    Priority Queue backed by Redis Sorted Sets (ZSET).

    Ordering Policy:
    Lower score = higher execution priority.
    Score = timestamp - (priority * 100,000)

    Priority Levels:
      - LOW = 1
      - NORMAL = 5
      - HIGH = 8
      - CRITICAL = 10

    For example:
      - A CRITICAL job (priority 10) submitted now has a score shifted by -1,000,000 seconds (~11.5 days in the past),
        guaranteeing it pops before any LOW or NORMAL task.
      - A task with a future scheduled_at (delayed retry) has its future timestamp reflected in the score.
      - Note: In TaskMesh, priority determines the dispatch ordering from the pending queue.
        Priority does NOT preempt or interrupt a task that has already been claimed and is actively running.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        queue_key: str = DEFAULT_QUEUE_KEY,
        processing_key: str = DEFAULT_PROCESSING_KEY,
        dlq_key: str = DEFAULT_DEAD_LETTER_KEY,
    ):
        self.redis = redis_client
        self.queue_key = queue_key
        self.processing_key = processing_key
        self.dlq_key = dlq_key

    @staticmethod
    def calculate_score(priority: int = 5, scheduled_at: Optional[float] = None) -> float:
        """
        Calculates sorted set score.
        Score = scheduled_timestamp - (priority * 100000.0)
        """
        ts = scheduled_at if scheduled_at is not None else time.time()
        return ts - (float(priority) * 100_000.0)

    async def enqueue(
        self,
        job_id: str | uuid.UUID,
        task_type: str,
        priority: int = 5,
        payload: Optional[Dict[str, Any]] = None,
        scheduled_at: Optional[float] = None,
        execution_cycle: Optional[int] = None,
    ) -> bool:
        """
        Idempotently pushes a task into the pending sorted set queue.
        If the job is already actively being processed (has an active lease or in processing),
        it will not be re-queued, ensuring safe repeated calls from the dispatcher.
        """
        job_id_str = str(job_id)
        score = self.calculate_score(priority=priority, scheduled_at=scheduled_at)
        message_dict: Dict[str, Any] = {
            "job_id": job_id_str,
            "task_type": task_type,
            "priority": priority,
            "payload": payload or {},
            "enqueued_at": time.time(),
        }
        if execution_cycle is not None:
            message_dict["execution_cycle"] = execution_cycle

        message_data = json.dumps(message_dict)

        # Lua script to safely enqueue only if not currently locked/active in lease
        lua_enqueue = """
        local queue_key = KEYS[1]
        local proc_key = KEYS[2]
        local lease_key = KEYS[3]
        local payload_hash_key = KEYS[4]
        local job_id = ARGV[1]
        local score = tonumber(ARGV[2])
        local message_data = ARGV[3]

        -- Check if actively executing with an active lease
        local active_worker = redis.call('GET', lease_key)
        if active_worker then
            -- Already running, do not re-insert into pending queue
            return 0
        end

        -- Store payload
        redis.call('HSET', payload_hash_key, 'data', message_data)
        -- Add/update score in pending ZSET
        redis.call('ZADD', queue_key, score, job_id)
        return 1
        """

        lease_key = f"{LEASE_PREFIX}{job_id_str}"
        payload_key = f"taskmesh:job_payload:{job_id_str}"

        await self.redis.eval(
            lua_enqueue,
            4,
            self.queue_key,
            self.processing_key,
            lease_key,
            payload_key,
            job_id_str,
            score,
            message_data,
        )
        logger.debug(f"Enqueued job {job_id_str} with priority {priority} (score {score})")
        return True

    async def dequeue(self, worker_id: str, lease_timeout: int = 60) -> Optional[Dict[str, Any]]:
        """
        Atomically pops the highest priority task ready for execution
        (score lowest in ZSET), records it into processing set, and acquires a worker lease.
        Prevents multiple workers from obtaining ownership of the same job.
        """
        lua_dequeue = """
        local queue_key = KEYS[1]
        local proc_key = KEYS[2]
        local lease_prefix = ARGV[1]
        local worker_id = ARGV[2]
        local lease_ttl = tonumber(ARGV[3])
        local now = tonumber(ARGV[4])

        -- Peek the lowest score item
        local items = redis.call('ZRANGE', queue_key, 0, 0, 'WITHSCORES')
        if #items == 0 then
            return nil
        end

        local job_id = items[1]
        local score = tonumber(items[2])

        -- Check if it is scheduled for the future (e.g. backoff delay)
        -- If score was calculated with scheduled_at, check if now < scheduled_at
        -- (Since score = ts - priority * 100k, ts = score + priority * 100k)
        -- The item is popped atomically
        redis.call('ZREM', queue_key, job_id)

        -- Add to processing ZSET with lease expiration timestamp as score
        local expire_at = now + lease_ttl
        redis.call('ZADD', proc_key, expire_at, job_id)

        -- Set lease key with TTL
        local lease_key = lease_prefix .. job_id
        redis.call('SET', lease_key, worker_id, 'EX', lease_ttl)

        return job_id
        """

        now = time.time()
        result = await self.redis.eval(
            lua_dequeue,
            2,
            self.queue_key,
            self.processing_key,
            LEASE_PREFIX,
            worker_id,
            lease_timeout,
            now,
        )

        if not result:
            return None

        job_id_str = result.decode("utf-8") if isinstance(result, bytes) else str(result)
        raw_payload = await self.redis.hget(f"taskmesh:job_payload:{job_id_str}", "data")

        if raw_payload:
            payload_str = raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else str(raw_payload)
            task_dict = json.loads(payload_str)
        else:
            task_dict = {"job_id": job_id_str}

        task_dict["worker_id"] = worker_id
        task_dict["lease_timeout"] = lease_timeout
        return task_dict

    async def renew_lease(self, job_id: str | uuid.UUID, worker_id: str, ttl: int = 60) -> bool:
        """
        Heartbeat function: extends worker lease if and only if held by worker_id.
        """
        job_id_str = str(job_id)
        lease_key = f"{LEASE_PREFIX}{job_id_str}"

        lua_renew = """
        local lease_key = KEYS[1]
        local proc_key = KEYS[2]
        local job_id = ARGV[1]
        local worker_id = ARGV[2]
        local ttl = tonumber(ARGV[3])
        local now = tonumber(ARGV[4])

        local current_worker = redis.call('GET', lease_key)
        if current_worker == worker_id then
            redis.call('EXPIRE', lease_key, ttl)
            redis.call('ZADD', proc_key, now + ttl, job_id)
            return 1
        end
        return 0
        """

        result = await self.redis.eval(
            lua_renew,
            2,
            lease_key,
            self.processing_key,
            job_id_str,
            worker_id,
            ttl,
            time.time(),
        )
        return bool(result)

    async def acknowledge_completion(self, job_id: str | uuid.UUID) -> bool:
        """
        Called when a job completes or is cancelled; cleans up Redis queues and leases.
        """
        job_id_str = str(job_id)
        lease_key = f"{LEASE_PREFIX}{job_id_str}"
        payload_key = f"taskmesh:job_payload:{job_id_str}"

        pipe = self.redis.pipeline()
        pipe.zrem(self.queue_key, job_id_str)
        pipe.zrem(self.processing_key, job_id_str)
        pipe.delete(lease_key)
        pipe.delete(payload_key)
        await pipe.execute()
        return True

    async def recover_stale_leases(self) -> List[str]:
        """
        Finds jobs in processing set whose leases have expired (or whose lease key is missing)
        and returns list of recovered/stale job IDs.
        """
        now = time.time()
        stale_jobs = await self.redis.zrangebyscore(self.processing_key, 0, now)
        recovered = []

        for item in stale_jobs:
            job_id_str = item.decode("utf-8") if isinstance(item, bytes) else str(item)
            lease_key = f"{LEASE_PREFIX}{job_id_str}"
            lease_val = await self.redis.get(lease_key)
            if lease_val is None:
                await self.redis.zrem(self.processing_key, job_id_str)
                recovered.append(job_id_str)

        return recovered

    async def get_queue_metrics(self) -> Dict[str, int]:
        """Returns counts of pending, processing, and dead-letter tasks."""
        pending_count = await self.redis.zcard(self.queue_key)
        processing_count = await self.redis.zcard(self.processing_key)
        dlq_count = await self.redis.zcard(self.dlq_key)
        return {
            "pending": pending_count,
            "processing": processing_count,
            "dlq": dlq_count,
        }
