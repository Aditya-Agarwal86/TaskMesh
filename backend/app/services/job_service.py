import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from pydantic import ValidationError as PydanticValidationError
from app.models.enums import JobStatus, OutboxStatus
from app.models.job import Job, JobEvent, OutboxEvent
from app.models.state_machine import validate_transition, InvalidStateTransitionError
from app.schemas.job import JobCreateRequest
from app.services.event_notifier import notify_event
from app.workers.task_registry import default_task_registry


class ConflictError(Exception):
    """Raised when an operation conflicts with the current state or idempotency specs."""
    pass


class InvalidJobPayloadError(Exception):
    """Raised when task_type is unsupported or payload fails schema validation."""
    def __init__(self, message: str, supported_types: Optional[List[str]] = None, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.supported_types = supported_types
        self.details = details


class NotFoundError(Exception):
    """Raised when a requested resource does not exist."""
    pass


class JobService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _check_payload_equivalence(self, existing_job: Job, request: JobCreateRequest, validated_payload: Optional[dict] = None) -> None:
        """
        Validates that a duplicate submission with the same idempotency_key matches
        the original task_type and payload. If materially different, raises ConflictError.
        """
        if existing_job.task_type != request.task_type:
            raise ConflictError(
                f"Idempotency key '{request.idempotency_key}' was previously used for task_type '{existing_job.task_type}', "
                f"which differs from requested '{request.task_type}'."
            )
        # Compare payload content (check against both raw request payload and validated payload)
        existing_payload = existing_job.payload or {}
        req_payload = request.payload or {}
        val_payload = validated_payload if validated_payload is not None else req_payload
        if existing_payload != req_payload and existing_payload != val_payload:
            raise ConflictError(
                f"Idempotency key '{request.idempotency_key}' reuse with materially different payload is prohibited."
            )

    async def create_job(self, request: JobCreateRequest) -> Tuple[Job, bool]:
        """
        Creates a job and an associated outbox event in a single atomic transaction.
        Validates task_type and input payload against authoritative registry schema first.
        If idempotency_key is provided:
          - If exists with identical payload/type, returns the existing job without creating a duplicate.
          - If exists with different payload/type, rejects with ConflictError.
          - Concurrency safe: Catches simultaneous duplicate race condition on commit and verifies equivalence.
        Returns:
            Tuple[Job, bool]: (job, created_flag) where created_flag is True if newly created.
        """
        # Validate task type and payload schema before creating any records
        if not default_task_registry.is_task_supported(request.task_type):
            supported = default_task_registry.get_supported_task_types()
            raise InvalidJobPayloadError(
                f"Unsupported task type '{request.task_type}'. Supported types: {supported}",
                supported_types=supported,
            )

        try:
            validated_payload = default_task_registry.validate_task_submission(
                request.task_type, request.payload or {}
            )
        except ValueError as ve:
            supported = default_task_registry.get_supported_task_types()
            raise InvalidJobPayloadError(str(ve), supported_types=supported)
        except PydanticValidationError as pve:
            errors = pve.errors()
            err_msg = "; ".join([f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in errors])
            raise InvalidJobPayloadError(
                f"Task payload validation failed for '{request.task_type}': {err_msg}",
                details={"errors": errors},
            )

        if request.idempotency_key:
            stmt = select(Job).where(Job.idempotency_key == request.idempotency_key)
            result = await self.db.execute(stmt)
            existing_job = result.scalar_one_or_none()
            if existing_job:
                self._check_payload_equivalence(existing_job, request, validated_payload)
                return existing_job, False

        job_id = uuid.uuid4()
        new_job = Job(
            id=job_id,
            task_type=request.task_type,
            payload=validated_payload,
            priority=request.priority,
            status=JobStatus.QUEUED.value,
            attempt_count=0,
            max_attempts=request.max_attempts,
            idempotency_key=request.idempotency_key,
        )

        initial_event = JobEvent(
            id=uuid.uuid4(),
            job_id=job_id,
            event_type="JOB_CREATED",
            metadata_payload={"priority": request.priority, "task_type": request.task_type},
        )

        outbox_event = OutboxEvent(
            id=uuid.uuid4(),
            job_id=job_id,
            event_type="JOB_QUEUED",
            payload={
                "job_id": str(job_id),
                "task_type": request.task_type,
                "priority": request.priority,
                "payload": request.payload,
                "execution_cycle": 1,
            },
            status=OutboxStatus.PENDING.value,
        )

        self.db.add(new_job)
        self.db.add(initial_event)
        self.db.add(outbox_event)

        try:
            await self.db.commit()
            await self.db.refresh(new_job)
            await notify_event(
                "job_update",
                {
                    "job_id": str(new_job.id),
                    "status": new_job.status,
                    "task_type": new_job.task_type,
                    "priority": new_job.priority,
                    "event_type": "JOB_CREATED",
                },
            )
            return new_job, True

        except IntegrityError:
            await self.db.rollback()
            if request.idempotency_key:
                # Concurrent insertion race condition resolution
                stmt = select(Job).where(Job.idempotency_key == request.idempotency_key)
                result = await self.db.execute(stmt)
                existing_job = result.scalar_one_or_none()
                if existing_job:
                    self._check_payload_equivalence(existing_job, request)
                    return existing_job, False
            raise

    async def get_job(self, job_id: uuid.UUID) -> Job:
        """Retrieves a job by ID."""
        stmt = select(Job).where(Job.id == job_id)
        result = await self.db.execute(stmt)
        job = result.scalar_one_or_none()
        if not job:
            raise NotFoundError(f"Job with ID {job_id} not found")
        return job

    async def list_jobs(
        self,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        priority: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Job], int]:
        """Returns paginated jobs based on filter criteria."""
        conditions = []
        if status:
            conditions.append(Job.status == status)
        if task_type:
            conditions.append(Job.task_type == task_type)
        if priority:
            conditions.append(Job.priority == priority)

        count_stmt = select(func.count(Job.id))
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        total_count = (await self.db.execute(count_stmt)).scalar() or 0

        query_stmt = select(Job)
        if conditions:
            query_stmt = query_stmt.where(and_(*conditions))
        query_stmt = (
            query_stmt.order_by(Job.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.db.execute(query_stmt)
        jobs = list(result.scalars().all())

        return jobs, total_count

    async def get_job_events(self, job_id: uuid.UUID) -> List[JobEvent]:
        """Retrieves full audit event history for a job."""
        await self.get_job(job_id)
        stmt = select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.created_at.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def cancel_job(self, job_id: uuid.UUID, reason: Optional[str] = None) -> Job:
        """
        Cancels a job if it is in QUEUED, RETRYING, or RUNNING state.
        Transitions status to CANCELLED and writes a cancellation event.
        Guarded by the state machine and row locking to prevent concurrent state corruption.
        """
        stmt = select(Job).where(Job.id == job_id).with_for_update()
        result = await self.db.execute(stmt)
        job = result.scalar_one_or_none()
        if not job:
            raise NotFoundError(f"Job with ID {job_id} not found")

        if job.status in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            raise ConflictError(f"Cannot cancel job in terminal state: {job.status}")

        try:
            validate_transition(job.status, JobStatus.CANCELLED, reason="User cancellation requested")
        except InvalidStateTransitionError as e:
            raise ConflictError(str(e))


        previous_status = job.status
        job.status = JobStatus.CANCELLED.value
        job.completed_at = datetime.now(timezone.utc)
        job.lease_expires_at = None
        job.next_retry_at = None

        cancel_event = JobEvent(
            id=uuid.uuid4(),
            job_id=job.id,
            event_type="JOB_CANCELLED",
            metadata_payload={"previous_status": previous_status, "reason": reason},
        )
        self.db.add(cancel_event)

        await self.db.commit()
        await self.db.refresh(job)
        await notify_event(
            "job_update",
            {
                "job_id": str(job.id),
                "status": job.status,
                "task_type": job.task_type,
                "event_type": "JOB_CANCELLED",
            },
        )
        return job

    async def retry_job(self, job_id: uuid.UUID) -> Job:
        """
        Manually triggers a retry for an eligible terminal failure (FAILED or CANCELLED).
        Resets status to QUEUED, updates next_retry_at, and writes an Outbox event.
        Guarded by row locks and state machine validation.
        """
        stmt = select(Job).where(Job.id == job_id).with_for_update()
        result = await self.db.execute(stmt)
        job = result.scalar_one_or_none()
        if not job:
            raise NotFoundError(f"Job with ID {job_id} not found")

        # Explicitly verify terminal state eligibility for manual retry
        if job.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            raise ConflictError(
                f"Job {job_id} in state '{job.status}' cannot be manually retried. "
                f"Only terminal failed or cancelled jobs can start a new execution cycle."
            )

        try:
            validate_transition(job.status, JobStatus.QUEUED, reason="Manual operator retry")
        except InvalidStateTransitionError as e:
            raise ConflictError(str(e))

        # Increment execution cycle for manual retry and reset current cycle attempt counter
        old_cycle = job.execution_cycle or 1
        new_cycle = old_cycle + 1
        job.execution_cycle = new_cycle
        job.attempt_count = 0  # Resets budget for the new cycle
        job.status = JobStatus.QUEUED.value
        job.next_retry_at = datetime.now(timezone.utc)
        job.completed_at = None
        job.error_type = None
        job.error_message = None
        job.error_details = None
        job.lease_expires_at = None
        job.worker_id = None

        retry_event = JobEvent(
            id=uuid.uuid4(),
            job_id=job.id,
            event_type="JOB_MANUAL_RETRY",
            metadata_payload={
                "cycle": new_cycle,
                "previous_cycle": old_cycle,
                "total_attempts_so_far": job.total_attempt_count or 0,
                "max_attempts_per_cycle": job.max_attempts,
            },
        )
        outbox_event = OutboxEvent(
            id=uuid.uuid4(),
            job_id=job.id,
            event_type="JOB_QUEUED",
            payload={
                "job_id": str(job.id),
                "task_type": job.task_type,
                "priority": job.priority,
                "payload": job.payload,
                "execution_cycle": new_cycle,
            },
            status=OutboxStatus.PENDING.value,
        )

        self.db.add(retry_event)
        self.db.add(outbox_event)

        await self.db.commit()
        await self.db.refresh(job)
        await notify_event(
            "job_update",
            {
                "job_id": str(job.id),
                "status": job.status,
                "task_type": job.task_type,
                "event_type": "JOB_MANUAL_RETRY",
            },
        )
        return job

