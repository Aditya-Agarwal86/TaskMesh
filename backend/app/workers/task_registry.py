import asyncio
import hashlib
import inspect
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError
from app.core.logging import get_logger

logger = get_logger("taskmesh.task_registry")

# Handler type: can take payload (Dict[str, Any]) and optional context (Dict[str, Any]), return Any
TaskHandler = Callable[..., Any]


class PermanentTaskError(ValueError):
    """
    Indicates a non-retryable failure (e.g. fatal data corruption, invalid format, or permanent rejection).
    When raised, workers must NOT retry the job and must immediately transition to FAILED.
    """
    pass



# ============================================================================
# Pydantic Schemas for Strict Input/Output Validation
# ============================================================================

class SleepTaskInput(BaseModel):
    duration_seconds: float = Field(default=1.0, ge=0.0, le=300.0, description="Duration in seconds to simulate work")
    checkpoint_interval: float = Field(default=0.5, ge=0.1, le=10.0, description="Cancellation check interval")


class SleepTaskOutput(BaseModel):
    slept_seconds: float
    status: str = "completed"


class GenerateReportInput(BaseModel):
    report_name: str = Field(min_length=1, max_length=128)
    dataset: list[dict[str, Any]] = Field(default_factory=list)
    format: str = Field(default="summary", pattern="^(summary|detailed|json)$")


class GenerateReportOutput(BaseModel):
    report_id: str
    report_name: str
    record_count: int
    data_hash: str
    summary_metrics: dict[str, Any]
    generated_at: float


class FailOnceInput(BaseModel):
    key: Optional[str] = Field(default=None, description="Optional custom key for tracking attempt state in DB")
    attempt_count: Optional[int] = Field(default=None, ge=1, description="Explicit attempt override if simulating")


class AlwaysFailInput(BaseModel):
    reason: Optional[str] = Field(default="Intentionally configured permanent failure for testing")


class EmailNotificationInput(BaseModel):
    to: str = Field(default="dev@example.com", min_length=3, description="Recipient email address")
    template: str = Field(default="default", min_length=1, description="Email notification template")


class InvoiceGenerationInput(BaseModel):
    order_id: int = Field(default=1001, ge=1, description="Order identifier for invoice generation")


class PdfRenderInput(BaseModel):
    document_id: str = Field(default="doc-1", min_length=1, description="Document identifier to render")


class DataSyncInput(BaseModel):
    records_count: int = Field(default=10, ge=1, le=100000, description="Count of records to synchronize")


class SafeTaskContext(BaseModel):
    """Context passed from worker to handler, providing access to checkpointing and persisted state."""
    job_id: str
    attempt_count: int
    max_attempts: int
    is_cancelled: Callable[[], Coroutine[Any, Any, bool]]


class TaskRegistry:
    """
    Registry for executable task handlers with input/output validation.
    Enforces that only registered, statically mapped tasks are executed.
    Arbitrary Python code or shell executions are strictly prevented.
    """

    def __init__(self):
        self._handlers: Dict[str, TaskHandler] = {}
        self._input_models: Dict[str, type[BaseModel]] = {}
        self._output_models: Dict[str, type[BaseModel]] = {}

    def register(
        self,
        task_type: str,
        input_model: Optional[type[BaseModel]] = None,
        output_model: Optional[type[BaseModel]] = None,
    ) -> Callable[[TaskHandler], TaskHandler]:
        """Decorator to register a task handler with optional input/output validation models."""
        def decorator(fn: TaskHandler) -> TaskHandler:
            self._handlers[task_type] = fn
            if input_model:
                self._input_models[task_type] = input_model
            if output_model:
                self._output_models[task_type] = output_model
            logger.info(f"Registered task handler for type '{task_type}': {fn.__name__}")
            return fn
        return decorator

    def add_handler(
        self,
        task_type: str,
        fn: TaskHandler,
        input_model: Optional[type[BaseModel]] = None,
        output_model: Optional[type[BaseModel]] = None,
    ) -> None:
        """Manually registers a task handler."""
        self._handlers[task_type] = fn
        if input_model:
            self._input_models[task_type] = input_model
        if output_model:
            self._output_models[task_type] = output_model
        logger.info(f"Registered task handler for type '{task_type}': {fn.__name__}")

    def get_handler(self, task_type: str) -> Optional[TaskHandler]:
        return self._handlers.get(task_type)

    def list_registered_tasks(self) -> Dict[str, str]:
        return {k: v.__name__ for k, v in self._handlers.items()}

    def is_task_supported(self, task_type: str) -> bool:
        """Returns True if task_type is registered."""
        return task_type in self._handlers

    def get_supported_task_types(self) -> List[str]:
        """Returns a sorted list of registered task type names."""
        return sorted(list(self._handlers.keys()))

    def validate_task_submission(self, task_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates task_type and payload before creating jobs or publishing outbox events.
        Raises ValueError if task_type is unsupported.
        Raises ValidationError if payload schema fails validation.
        Returns the sanitized/validated payload dict.
        """
        if not self.is_task_supported(task_type):
            supported = sorted(list(self._handlers.keys()))
            raise ValueError(
                f"Unsupported task type '{task_type}'. Supported types: {supported}"
            )

        input_model = self._input_models.get(task_type)
        if input_model:
            try:
                model_instance = input_model.model_validate(payload)
                return model_instance.model_dump()
            except ValidationError as ve:
                err_msg = "; ".join([f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in ve.errors()])
                raise ValueError(f"Task '{task_type}' payload validation failed: {err_msg}")
        return payload

    def get_supported_task_schemas(self) -> Dict[str, Dict[str, Any]]:
        """Returns metadata and JSON schema for all registered task types."""
        result = {}
        for task_name, handler in self._handlers.items():
            input_model = self._input_models.get(task_name)
            schema = input_model.model_json_schema() if input_model else {"type": "object"}
            result[task_name] = {
                "task_type": task_name,
                "handler_name": handler.__name__,
                "doc": handler.__doc__.strip() if handler.__doc__ else "",
                "schema": schema,
            }
        return result

    async def execute(
        self,
        task_type: str,
        payload: Dict[str, Any],
        context: Optional[SafeTaskContext] = None,
    ) -> Any:
        """
        Executes the registered handler for task_type after validating input payload,
        and validates output payload against schema if defined.
        """
        handler = self.get_handler(task_type)
        if not handler:
            raise PermanentTaskError(f"Security error: No task handler registered for task type: '{task_type}'. Permanent failure.")

        # 1. Validate Input Payload
        input_model = self._input_models.get(task_type)
        validated_input = payload
        if input_model:
            try:
                parsed_model = input_model.model_validate(payload)
                validated_input = parsed_model.model_dump()
            except ValidationError as ve:
                raise PermanentTaskError(f"Task '{task_type}' payload validation failed: {ve.errors()}")


        # 2. Inspect handler signature for context injection
        sig = inspect.signature(handler)
        kwargs: Dict[str, Any] = {"payload": validated_input}
        if "context" in sig.parameters and context is not None:
            kwargs["context"] = context

        # 3. Execute handler
        if inspect.iscoroutinefunction(handler):
            raw_output = await handler(**kwargs)
        else:
            loop = asyncio.get_running_loop()
            raw_output = await loop.run_in_executor(None, lambda: handler(**kwargs))

        # 4. Validate Output
        output_model = self._output_models.get(task_type)
        if output_model and isinstance(raw_output, dict):
            try:
                validated_out = output_model.model_validate(raw_output)
                return validated_out.model_dump()
            except ValidationError as ve:
                raise ValueError(f"Task '{task_type}' output validation failed: {ve.errors()}")

        return raw_output


# Global default task registry
default_task_registry = TaskRegistry()


# ============================================================================
# Core Demo Tasks
# ============================================================================

@default_task_registry.register("sleep", input_model=SleepTaskInput, output_model=SleepTaskOutput)
async def handle_sleep(payload: Dict[str, Any], context: Optional[SafeTaskContext] = None) -> Dict[str, Any]:
    """
    Simulates work for a configurable duration.
    Periodically checks if the job was cancelled at safe checkpoints.
    """
    duration = float(payload.get("duration_seconds", 1.0))
    checkpoint_interval = float(payload.get("checkpoint_interval", 0.5))

    elapsed = 0.0
    while elapsed < duration:
        if context is not None and await context.is_cancelled():
            logger.info(f"Task sleep for job {context.job_id} observed cancellation at checkpoint ({elapsed:.1f}s / {duration:.1f}s)")
            raise asyncio.CancelledError(f"Task cancelled by user request after {elapsed:.1f}s")

        step = min(checkpoint_interval, duration - elapsed)
        await asyncio.sleep(step)
        elapsed += step

    return {"slept_seconds": elapsed, "status": "completed"}


@default_task_registry.register("generate_report", input_model=GenerateReportInput, output_model=GenerateReportOutput)
async def handle_generate_report(payload: Dict[str, Any], context: Optional[SafeTaskContext] = None) -> Dict[str, Any]:
    """
    Generates a deterministic sample report from input dataset.
    """
    report_name = payload["report_name"]
    dataset = payload.get("dataset", [])
    format_type = payload.get("format", "summary")

    # Deterministic hash of dataset
    data_str = str(sorted(str(d) for d in dataset))
    data_hash = hashlib.sha256(data_str.encode("utf-8")).hexdigest()[:16]

    # Compute deterministic metrics
    numeric_values = [
        val for item in dataset if isinstance(item, dict) for val in item.values() if isinstance(val, (int, float))
    ]
    total_val = sum(numeric_values) if numeric_values else 0
    avg_val = (total_val / len(numeric_values)) if numeric_values else 0.0

    return {
        "report_id": f"rep-{data_hash}",
        "report_name": report_name,
        "record_count": len(dataset),
        "data_hash": data_hash,
        "summary_metrics": {
            "total": total_val,
            "average": round(avg_val, 2),
            "numeric_fields_evaluated": len(numeric_values),
            "format": format_type,
        },
        "generated_at": time.time(),
    }


@default_task_registry.register("fail_once", input_model=FailOnceInput)
async def handle_fail_once(payload: Dict[str, Any], context: Optional[SafeTaskContext] = None) -> Dict[str, Any]:
    """
    Fails on the first attempt (attempt_count == 1) and succeeds on subsequent attempts.
    Uses the persisted attempt_count from PostgreSQL/Job record via context,
    ensuring it does NOT rely on ephemeral process-local memory.
    """
    attempt = context.attempt_count if context is not None else int(payload.get("attempt_count", 1))

    if attempt <= 1:
        logger.warning(f"Task 'fail_once' failing intentionally on persisted attempt #{attempt}")
        raise RuntimeError(f"Simulated transient error on attempt #{attempt}. Retry should succeed.")

    logger.info(f"Task 'fail_once' succeeding on persisted attempt #{attempt}")
    return {
        "status": "succeeded",
        "attempt_succeeded": attempt,
        "message": f"Successfully recovered and executed on attempt #{attempt}",
    }


@default_task_registry.register("always_fail", input_model=AlwaysFailInput)
async def handle_always_fail(payload: Dict[str, Any], context: Optional[SafeTaskContext] = None) -> Dict[str, Any]:
    """
    Consistently fails to test retry exhaustion and Dead Letter Queue / FAILED transitions.
    """
    reason = payload.get("reason", "Intentionally configured permanent failure for testing")
    attempt = context.attempt_count if context is not None else 1
    logger.error(f"Task 'always_fail' triggering failure on attempt #{attempt}: {reason}")
    raise RuntimeError(f"Permanent task failure on attempt #{attempt}: {reason}")


# Standard utility tasks for API demonstrations
@default_task_registry.register("email_notification", input_model=EmailNotificationInput)
async def handle_email_notification(payload: Dict[str, Any], context: Optional[SafeTaskContext] = None) -> Dict[str, Any]:
    to_email = payload.get("to", "unknown@example.com")
    template = payload.get("template", "default")
    await asyncio.sleep(0.05)
    return {
        "status": "delivered",
        "recipient": to_email,
        "template": template,
        "message_id": f"msg_{hash(to_email) & 0xffffff}",
    }


@default_task_registry.register("invoice_generation", input_model=InvoiceGenerationInput)
async def handle_invoice_generation(payload: Dict[str, Any], context: Optional[SafeTaskContext] = None) -> Dict[str, Any]:
    order_id = payload.get("order_id", 0)
    await asyncio.sleep(0.05)
    return {
        "invoice_number": f"INV-{order_id}-2026",
        "total_amount": 199.99,
        "currency": "USD",
        "url": f"https://cdn.taskmesh.internal/invoices/{order_id}.pdf",
    }


@default_task_registry.register("pdf_render", input_model=PdfRenderInput)
async def handle_pdf_render(payload: Dict[str, Any], context: Optional[SafeTaskContext] = None) -> Dict[str, Any]:
    doc_id = payload.get("document_id", "doc-1")
    await asyncio.sleep(0.05)
    return {
        "rendered_pages": 3,
        "file_size_bytes": 142050,
        "doc_id": doc_id,
    }


@default_task_registry.register("data_sync", input_model=DataSyncInput)
async def handle_data_sync(payload: Dict[str, Any], context: Optional[SafeTaskContext] = None) -> Dict[str, Any]:
    records = payload.get("records_count", 10)
    await asyncio.sleep(0.05)
    return {
        "synced_records": records,
        "status": "success",
    }
