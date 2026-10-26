import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    String,
    Integer,
    DateTime,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models.enums import JobStatus, JobPriority, OutboxStatus


class Base(DeclarativeBase):
    pass


# Use JSON type that supports both PostgreSQL JSONB and SQLite JSON for tests
JsonType = JSON().with_variant(JSONB, "postgresql")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    result: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonType, nullable=True)

    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=JobPriority.NORMAL.value, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobStatus.QUEUED.value, index=True
    )

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    execution_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    error_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    error_details: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonType, nullable=True)

    # Relationships
    events: Mapped[List["JobEvent"]] = relationship(
        "JobEvent", back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.created_at"
    )
    outbox_events: Mapped[List["OutboxEvent"]] = relationship(
        "OutboxEvent", back_populates="job", cascade="all, delete-orphan"
    )
    diagnostics: Mapped[List["FailureDiagnostic"]] = relationship(
        "FailureDiagnostic", back_populates="job", cascade="all, delete-orphan", order_by="FailureDiagnostic.created_at"
    )

    __table_args__ = (
        Index("ix_jobs_status_priority", "status", "priority"),
        Index("ix_jobs_status_next_retry", "status", "next_retry_at"),
        Index("ix_jobs_status_lease", "status", "lease_expires_at"),
    )


class FailureDiagnostic(Base):
    __tablename__ = "failure_diagnostics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model: Mapped[str] = mapped_column(String(64), nullable=False)

    error_category: Mapped[str] = mapped_column(String(64), nullable=False)
    likely_cause: Mapped[str] = mapped_column(String(512), nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    recommended_next_steps: Mapped[List[str]] = mapped_column(JsonType, nullable=False, default=list)
    retry_may_help: Mapped[bool] = mapped_column(nullable=False, default=False)
    reasoning_summary: Mapped[str] = mapped_column(String(2048), nullable=False)

    raw_response: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    job: Mapped["Job"] = relationship("Job", back_populates="diagnostics")

    __table_args__ = (
        Index("ix_failure_diagnostics_job_attempt", "job_id", "attempt"),
    )



class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    job: Mapped["Job"] = relationship("Job", back_populates="events")

    __table_args__ = (
        Index("ix_job_events_job_created", "job_id", "created_at"),
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OutboxStatus.PENDING.value, index=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    job: Mapped["Job"] = relationship("Job", back_populates="outbox_events")

    __table_args__ = (
        Index("ix_outbox_status_created", "status", "created_at"),
    )
