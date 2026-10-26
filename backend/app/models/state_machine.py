from typing import Dict, Set
from app.models.enums import JobStatus

# Centralized Finite State Machine transition rules
# Source status -> set of valid target statuses
VALID_TRANSITIONS: Dict[JobStatus, Set[JobStatus]] = {
    JobStatus.QUEUED: {
        JobStatus.RUNNING,      # Claimed by a worker
        JobStatus.CANCELLED,    # Cancelled before pickup
    },
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,    # Execution completed successfully
        JobStatus.RETRYING,     # Retryable failure (retry budget remaining) or lease expired
        JobStatus.FAILED,       # Terminal failure (retries exhausted or unrecoverable error)
        JobStatus.CANCELLED,    # Cancelled at cooperative checkpoint
    },
    JobStatus.RETRYING: {
        JobStatus.RUNNING,      # Dequeued by a worker for next retry attempt
        JobStatus.QUEUED,       # Backoff delay elapsed, returned to active queue
        JobStatus.CANCELLED,    # Cancelled while waiting for backoff
    },

    JobStatus.SUCCEEDED: set(),  # Terminal state: cannot transition further
    JobStatus.FAILED: {
        JobStatus.QUEUED,       # Explicit manual retry requested by user/operator
    },
    JobStatus.CANCELLED: {
        JobStatus.QUEUED,       # Explicit manual retry requested by user/operator
    },
}


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal state transition is attempted."""
    def __init__(self, current_status: str, target_status: str, reason: str = ""):
        msg = f"Invalid state transition: Cannot transition job from '{current_status}' to '{target_status}'."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg)
        self.current_status = current_status
        self.target_status = target_status


def validate_transition(current_status: str | JobStatus, target_status: str | JobStatus, reason: str = "") -> None:
    """
    Validates whether transition from current_status to target_status is permitted.
    Raises InvalidStateTransitionError if illegal.
    """
    curr = JobStatus(current_status) if isinstance(current_status, str) else current_status
    tgt = JobStatus(target_status) if isinstance(target_status, str) else target_status

    if curr == tgt:
        # Idempotent re-affirmation is allowed
        return

    allowed_targets = VALID_TRANSITIONS.get(curr, set())
    if tgt not in allowed_targets:
        raise InvalidStateTransitionError(curr.value, tgt.value, reason=reason)
