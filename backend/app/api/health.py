import datetime
import json
from typing import Dict, List, Optional
from fastapi import APIRouter, status, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select, func, case

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis_client
from app.db.session import get_db
from app.models.enums import JobStatus
from app.models.job import Job
from app.queue.priority_queue import RedisPriorityQueue

logger = get_logger("taskmesh.health_api")

router = APIRouter(prefix="/health", tags=["Health & Observability"])


class LiveResponse(BaseModel):
    status: str
    service: str
    environment: str


class ReadyResponse(BaseModel):
    status: str
    database: str
    redis: str


class WorkerHeartbeatInfo(BaseModel):
    worker_id: str
    last_heartbeat: str
    status: str
    current_job_id: Optional[str] = None
    lease_expires_at: Optional[str] = None


class ExecutionDurationMetrics(BaseModel):
    avg_seconds: float
    min_seconds: float
    max_seconds: float
    sample_count: int


class SystemMetricsResponse(BaseModel):
    queue_depth: Dict[str, int]
    job_counts_by_status: Dict[str, int]
    service_status: str
    total_jobs: int
    processed_jobs: int
    succeeded_jobs: int
    completed_jobs: int  # Synonym for succeeded_jobs for backward compatibility
    failed_jobs: int
    cancelled_jobs: int
    terminal_jobs: int
    total_retries: int
    active_workers_count: int
    workers: List[WorkerHeartbeatInfo]
    execution_duration: ExecutionDurationMetrics


@router.get("/live", response_model=LiveResponse, status_code=status.HTTP_200_OK)
async def liveness() -> LiveResponse:
    """Liveness probe: returns 200 if the web process is running."""
    return LiveResponse(
        status="ok",
        service="taskmesh-api",
        environment=settings.ENVIRONMENT,
    )


@router.get("/ready", response_model=ReadyResponse, status_code=status.HTTP_200_OK)
async def readiness(db: AsyncSession = Depends(get_db)) -> ReadyResponse:
    """Readiness probe: validates database and Redis connectivity."""
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    redis_status = "ok"
    try:
        redis_client = await get_redis_client()
        await redis_client.ping()
    except Exception as e:
        redis_status = f"unhealthy: {str(e)}"

    is_ready = (db_status == "ok") and (redis_status == "ok")
    return ReadyResponse(
        status="ok" if is_ready else "degraded",
        database=db_status,
        redis=redis_status,
    )


@router.get("/metrics", response_model=SystemMetricsResponse, status_code=status.HTTP_200_OK)
async def metrics(db: AsyncSession = Depends(get_db)) -> SystemMetricsResponse:
    """
    Comprehensive operational metrics endpoint exposing:
    - Queue depth: Redis pending priority queue, active leases, and DLQ.
    - Status breakdown: QUEUED, RUNNING, RETRYING, SUCCEEDED, FAILED, CANCELLED.
    - Worker telemetry: Active worker IDs, heartbeat timestamps, and current leases.
    - Completed jobs, failed jobs, and total retries recorded.
    - Execution duration statistics (avg, min, max) calculated from actual completed jobs.
    """
    # 1. Fetch Redis queue metrics
    redis_client = await get_redis_client()
    queue = RedisPriorityQueue(redis_client)
    try:
        queue_metrics = await queue.get_queue_metrics()
    except Exception:
        queue_metrics = {"pending": 0, "processing": 0, "dlq": 0}

    # 2. Fetch PostgreSQL job status breakdown
    stmt = select(Job.status, func.count(Job.id)).group_by(Job.status)
    result = await db.execute(stmt)
    status_counts = {row[0]: row[1] for row in result.all()}

    # Ensure all enum keys are present in map
    for s in JobStatus:
        if s.value not in status_counts:
            status_counts[s.value] = 0

    # 3. Calculate total retries and completed/failed counts
    retries_stmt = select(func.sum(case((Job.attempt_count > 1, Job.attempt_count - 1), else_=0)))
    total_retries_res = await db.execute(retries_stmt)
    total_retries = total_retries_res.scalar() or 0


    succeeded_jobs = status_counts.get(JobStatus.SUCCEEDED.value, 0)
    failed_jobs = status_counts.get(JobStatus.FAILED.value, 0)
    cancelled_jobs = status_counts.get(JobStatus.CANCELLED.value, 0)
    running_jobs_count = status_counts.get(JobStatus.RUNNING.value, 0)
    retrying_jobs_count = status_counts.get(JobStatus.RETRYING.value, 0)
    queued_jobs_count = status_counts.get(JobStatus.QUEUED.value, 0)

    # Distinct categories:
    # 1. total_jobs: All jobs in the system across all states
    total_jobs = sum(status_counts.values())
    # 2. terminal_jobs: Jobs that have reached a terminal outcome
    terminal_jobs = succeeded_jobs + failed_jobs + cancelled_jobs
    # 3. processed_jobs: Jobs that have been picked up and processed (succeeded, failed, or currently running/retrying)
    processed_jobs = succeeded_jobs + failed_jobs + running_jobs_count + retrying_jobs_count

    # 4. Calculate execution duration from completed jobs with both started_at and completed_at
    completed_jobs_stmt = (
        select(Job.started_at, Job.completed_at)
        .where(
            Job.status == JobStatus.SUCCEEDED.value,
            Job.started_at.isnot(None),
            Job.completed_at.isnot(None),
        )
        .order_by(Job.completed_at.desc())
        .limit(50)
    )
    completed_times_res = await db.execute(completed_jobs_stmt)
    durations = [
        (row[1] - row[0]).total_seconds()
        for row in completed_times_res.all()
        if row[0] is not None and row[1] is not None and (row[1] - row[0]).total_seconds() >= 0
    ]

    if durations:
        duration_metrics = ExecutionDurationMetrics(
            avg_seconds=round(sum(durations) / len(durations), 2),
            min_seconds=round(min(durations), 2),
            max_seconds=round(max(durations), 2),
            sample_count=len(durations),
        )
    else:
        duration_metrics = ExecutionDurationMetrics(
            avg_seconds=0.0,
            min_seconds=0.0,
            max_seconds=0.0,
            sample_count=0,
        )

    # 5. Discover active workers & heartbeats from Redis presence registry & running leases
    running_jobs_stmt = select(Job).where(Job.status == JobStatus.RUNNING.value)
    running_jobs_res = await db.execute(running_jobs_stmt)
    running_jobs = list(running_jobs_res.scalars().all())
    running_by_worker = {rj.worker_id: rj for rj in running_jobs if rj.worker_id}

    worker_list: List[WorkerHeartbeatInfo] = []
    seen_workers = set()

    # Query online worker heartbeat keys from Redis
    try:
        registered_worker_ids = await redis_client.smembers("taskmesh:active_workers")
        for wid_raw in registered_worker_ids:
            wid = wid_raw.decode("utf-8") if isinstance(wid_raw, bytes) else str(wid_raw)
            heartbeat_raw = await redis_client.get(f"taskmesh:workers:{wid}")
            if not heartbeat_raw:
                # Key expired: remove from active set
                await redis_client.srem("taskmesh:active_workers", wid)
                continue

            hb_data = {}
            try:
                hb_data = json.loads(heartbeat_raw)
            except Exception:
                pass

            seen_workers.add(wid)
            active_job = running_by_worker.get(wid)
            worker_list.append(
                WorkerHeartbeatInfo(
                    worker_id=wid,
                    last_heartbeat=hb_data.get("heartbeat_at", datetime.datetime.now(datetime.timezone.utc).isoformat()),
                    status="BUSY" if active_job else "IDLE",
                    current_job_id=str(active_job.id) if active_job else None,
                    lease_expires_at=active_job.lease_expires_at.isoformat() if active_job and active_job.lease_expires_at else None,
                )
            )
    except Exception as e:
        logger.debug(f"Error querying Redis worker presence: {e}")

    # Also include any worker holding a database running lease that wasn't registered in Redis yet
    for wid, rj in running_by_worker.items():
        if wid not in seen_workers:
            seen_workers.add(wid)
            worker_list.append(
                WorkerHeartbeatInfo(
                    worker_id=wid,
                    last_heartbeat=(rj.updated_at or rj.created_at).isoformat(),
                    status="BUSY",
                    current_job_id=str(rj.id),
                    lease_expires_at=rj.lease_expires_at.isoformat() if rj.lease_expires_at else None,
                )
            )

    return SystemMetricsResponse(
        queue_depth=queue_metrics,
        job_counts_by_status=status_counts,
        service_status="healthy",
        total_jobs=total_jobs,
        processed_jobs=processed_jobs,
        succeeded_jobs=succeeded_jobs,
        completed_jobs=succeeded_jobs,  # Backward compatibility synonym
        failed_jobs=failed_jobs,
        cancelled_jobs=cancelled_jobs,
        terminal_jobs=terminal_jobs,
        total_retries=int(total_retries),
        active_workers_count=len(worker_list),
        workers=worker_list,
        execution_duration=duration_metrics,
    )

