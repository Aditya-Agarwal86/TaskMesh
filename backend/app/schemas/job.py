import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import JobStatus, JobPriority


class JobCreateRequest(BaseModel):
    task_type: str = Field(..., min_length=1, max_length=128, description="Type identifier for the task handler")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Input parameters for the task")
    priority: int = Field(default=JobPriority.NORMAL.value, ge=1, le=10, description="Task priority (1-10)")
    max_attempts: int = Field(default=3, ge=1, le=10, description="Maximum execution attempts")
    idempotency_key: Optional[str] = Field(None, max_length=255, description="Unique key to prevent duplicate submissions")


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_type: str
    payload: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    priority: int
    status: JobStatus
    attempt_count: int
    max_attempts: int
    execution_cycle: int = 1
    total_attempt_count: int = 0
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    worker_id: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None


class JobSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_type: str
    priority: int
    status: JobStatus
    attempt_count: int
    max_attempts: int
    execution_cycle: int = 1
    total_attempt_count: int = 0
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class JobEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    event_type: str
    metadata_payload: Optional[Dict[str, Any]] = None
    created_at: datetime


class PaginatedJobsResponse(BaseModel):
    items: List[JobResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class FailureDiagnosticResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    attempt: int
    model: str
    error_category: str
    likely_cause: str
    confidence: str
    recommended_next_steps: List[str]
    retry_may_help: bool
    reasoning_summary: str
    created_at: datetime

