import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.enums import JobStatus
from app.schemas.job import (
    JobCreateRequest,
    JobResponse,
    JobEventResponse,
    PaginatedJobsResponse,
    ErrorResponse,
    FailureDiagnosticResponse,
)
from app.services.job_service import JobService, NotFoundError, ConflictError, InvalidJobPayloadError
from app.services.diagnostics_service import GeminiDiagnosticsService
from app.workers.task_registry import default_task_registry


router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get(
    "/task-types",
    status_code=status.HTTP_200_OK,
    summary="List supported task types and schemas",
    description="Returns the authoritative list of supported task handlers and their input payload schemas.",
)
async def list_task_types():
    return default_task_registry.get_supported_task_schemas()


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        409: {"model": ErrorResponse, "description": "Conflict or duplicate state"},
        422: {"model": ErrorResponse, "description": "Unsupported task type or invalid payload schema"},
    },
    summary="Submit a new job",
    description="Validates the task specification and durably records the job and its outbox event in PostgreSQL within a single atomic transaction.",
)
async def submit_job(
    request: JobCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    service = JobService(db)
    try:
        job, created = await service.create_job(request)
        return job
    except ConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": str(e)},
        )
    except InvalidJobPayloadError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "UNSUPPORTED_TASK_TYPE_OR_INVALID_PAYLOAD", "message": str(e)},
        )



@router.get(
    "",
    response_model=PaginatedJobsResponse,
    status_code=status.HTTP_200_OK,
    summary="List jobs",
    description="Retrieve paginated jobs with optional filtering by status, task_type, and priority.",
)
async def list_jobs(
    status_filter: Optional[JobStatus] = Query(None, alias="status", description="Filter by job status"),
    task_type: Optional[str] = Query(None, description="Filter by task type"),
    priority: Optional[int] = Query(None, ge=1, le=10, description="Filter by priority"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    service = JobService(db)
    status_val = status_filter.value if status_filter else None
    jobs, total = await service.list_jobs(
        status=status_val,
        task_type=task_type,
        priority=priority,
        page=page,
        page_size=page_size,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return PaginatedJobsResponse(
        items=[JobResponse.model_validate(j) for j in jobs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    status_code=status.HTTP_200_OK,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
    summary="Get job details",
    description="Fetch authoritative record and current status for a single job.",
)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    service = JobService(db)
    try:
        return await service.get_job(job_id)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": str(e)},
        )


@router.get(
    "/{job_id}/events",
    response_model=list[JobEventResponse],
    status_code=status.HTTP_200_OK,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
    summary="Get job event history",
    description="Audit timeline of all state transitions and system events associated with the job.",
)
async def get_job_events(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    service = JobService(db)
    try:
        return await service.get_job_events(job_id)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": str(e)},
        )


@router.post(
    "/{job_id}/retry",
    response_model=JobResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
        409: {"model": ErrorResponse, "description": "Job cannot be retried in its current state"},
    },
    summary="Manually retry a job",
    description="Resets a FAILED or CANCELLED job back to QUEUED and registers a new Outbox dispatch event.",
)
async def retry_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    service = JobService(db)
    try:
        return await service.retry_job(job_id)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": str(e)},
        )
    except ConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVALID_STATE_FOR_RETRY", "message": str(e)},
        )


@router.post(
    "/{job_id}/cancel",
    response_model=JobResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
        409: {"model": ErrorResponse, "description": "Job already in terminal state"},
    },
    summary="Cancel a job",
    description="Cancels an active or pending job and records a cancellation audit event.",
)
async def cancel_job(
    job_id: uuid.UUID,
    reason: Optional[str] = Query(None, description="Optional cancellation reason"),
    db: AsyncSession = Depends(get_db),
):
    service = JobService(db)
    try:
        return await service.cancel_job(job_id, reason=reason)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": str(e)},
        )
    except ConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "JOB_ALREADY_TERMINAL", "message": str(e)},
        )


@router.get(
    "/{job_id}/diagnostics",
    response_model=Optional[FailureDiagnosticResponse],
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
    },
    summary="Get job failure diagnostics",
    description="Fetches stored Gemini failure diagnostics for a failed or retrying job.",
)
async def get_job_diagnostics(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    service = GeminiDiagnosticsService(db)
    diagnostic = await service.diagnose_job_failure(job_id=job_id, force_refresh=False)
    return diagnostic


@router.post(
    "/{job_id}/diagnostics/analyze",
    response_model=Optional[FailureDiagnosticResponse],
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
        503: {"model": ErrorResponse, "description": "AI diagnostics service unavailable or unconfigured"},
    },
    summary="Request Gemini AI failure diagnostics",
    description="Invokes Google Gemini with sanitized failure context to produce structured root-cause recommendations.",
)
async def analyze_job_failure(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    service = GeminiDiagnosticsService(db)
    diagnostic = await service.diagnose_job_failure(job_id=job_id, force_refresh=True)
    if not diagnostic:
        err_msg = service.last_error or "AI failure diagnostics could not be generated (ensure GEMINI_API_KEY is configured and job has recorded failure telemetry)."
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DIAGNOSTICS_UNAVAILABLE",
                "message": err_msg,
            },
        )
    return diagnostic


