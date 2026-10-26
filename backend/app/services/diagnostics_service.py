import asyncio
import json
import re
from typing import Any, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.job import Job, FailureDiagnostic

genai: Any
try:
    from google import genai
except ImportError:
    genai = None

logger = get_logger("taskmesh.diagnostics")

# Structured diagnostic schema expected from Gemini
class DiagnosticOutput(BaseModel):
    error_category: str = Field(
        ...,
        description="High level category, e.g. CONFIGURATION, NETWORK, RESOURCE_EXHAUSTION, PERMISSION, TRANSIENT, VALIDATION",
    )
    likely_cause: str = Field(..., description="Concise explanation of the likely root cause")
    confidence: str = Field(
        ...,
        description="Confidence or uncertainty assessment: HIGH, MEDIUM, or LOW",
    )
    recommended_next_steps: List[str] = Field(
        default_factory=list,
        description="Actionable remediation steps for the engineer",
    )
    retry_may_help: bool = Field(
        ...,
        description="Whether retrying without code/configuration changes may succeed",
    )
    reasoning_summary: str = Field(
        ...,
        description="Technical reasoning summary appropriate for a developer",
    )


# Sanitization regexes to redact sensitive tokens/secrets
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|auth|bearer|credentials|private[_-]?key|ssh[_-]?key)"
)
JWT_PATTERN = re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}")
BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{15,}")


def sanitize_data(data: Any) -> Any:
    """Recursively redacts secrets and sensitive key-values from payloads."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if SENSITIVE_KEY_PATTERN.search(str(k)):
                sanitized[k] = "[REDACTED_SECRET]"
            else:
                sanitized[k] = sanitize_data(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    elif isinstance(data, str):
        # Mask obvious tokens
        text = JWT_PATTERN.sub("[REDACTED_JWT]", data)
        text = BEARER_PATTERN.sub("Bearer [REDACTED_TOKEN]", text)
        return text
    return data


class GeminiDiagnosticsService:
    """
    Failure Diagnostics Service utilizing Google Gemini API:
    - Bounded retries and timeouts for Gemini API calls.
    - Sanitizes payloads before transmission (removes credentials, tokens, PII).
    - Asks Gemini for strict JSON structured output matching DiagnosticOutput.
    - Validates model output using Pydantic schema (untrusted data).
    - Persists diagnostic record in PostgreSQL associated with the job and failure attempt.
    - Never executes AI code or commands; never overrides queue retry/cancellation rules.
    - Handles unavailable credentials or API errors gracefully.
    """

    def __init__(self, db: AsyncSession, client: Optional[Any] = None):
        self.db = db
        self.client = client
        self.last_error: Optional[str] = None

    def _get_client(self) -> Optional[Any]:
        if self.client is not None:
            return self.client

        api_key = settings.GEMINI_API_KEY
        if not api_key:
            self.last_error = "Gemini API key is not configured in the application environment (GEMINI_API_KEY)."
            return None

        try:
            from google import genai
            return genai.Client(api_key=api_key)
        except Exception as e:
            self.last_error = f"Failed to instantiate Google GenAI client: {e}"
            logger.warning(self.last_error)
            return None

    async def diagnose_job_failure(
        self,
        job_id: Any,
        force_refresh: bool = False,
    ) -> Optional[FailureDiagnostic]:
        """
        Generates or retrieves structured failure diagnostics for a job.
        If a diagnostic already exists for the job's current attempt and force_refresh is False,
        returns the existing diagnostic from PostgreSQL.
        """
        stmt = select(Job).where(Job.id == job_id)
        result = await self.db.execute(stmt)
        job = result.scalar_one_or_none()

        if not job:
            return None

        if not job.error_type and not job.error_message and job.status not in ("FAILED", "RETRYING"):
            return None

        attempt = max(1, job.attempt_count)

        # Check existing diagnostic
        if not force_refresh:
            diag_stmt = (
                select(FailureDiagnostic)
                .where(FailureDiagnostic.job_id == job.id, FailureDiagnostic.attempt == attempt)
                .order_by(FailureDiagnostic.created_at.desc())
            )
            diag_res = await self.db.execute(diag_stmt)
            existing_diag = diag_res.scalar_one_or_none()
            if existing_diag:
                return existing_diag

        client = self._get_client()
        if client is None:
            logger.info(f"Gemini API key not configured or client unavailable; skipping AI diagnostics for {job.id}")
            return None

        # Build sanitized failure diagnostic context
        sanitized_payload = sanitize_data(job.payload or {})
        sanitized_details = sanitize_data(job.error_details or {})

        context_info = {
            "task_type": job.task_type,
            "status": job.status,
            "attempt": attempt,
            "max_attempts": job.max_attempts,
            "error_type": job.error_type or "UnknownError",
            "error_message": job.error_message or "No error message recorded",
            "sanitized_payload_keys": list(sanitized_payload.keys()),
            "sanitized_error_details": sanitized_details,
        }

        prompt = f"""You are a distributed systems and backend engineer diagnosing a background task failure in TaskMesh.
Analyze the following sanitized failure telemetry and return strict JSON conforming to the schema.

FAILURE TELEMETRY:
{json.dumps(context_info, indent=2)}

SCHEMA INSTRUCTIONS:
Return a single JSON object with the following fields:
- "error_category": string (e.g. "CONFIGURATION", "NETWORK", "TRANSIENT", "RESOURCE_EXHAUSTION", "DATA_VALIDATION", "PERMISSION", "TIMEOUT", "APPLICATION_BUG")
- "likely_cause": string (concise explanation of likely root cause)
- "confidence": string ("HIGH", "MEDIUM", or "LOW")
- "recommended_next_steps": list of strings (concrete remediation recommendations for the developer)
- "retry_may_help": boolean (true if automatic or manual retry without code change could succeed)
- "reasoning_summary": string (technical summary explaining the deduction)

Do NOT execute code or produce executable commands without caution. Provide analysis only.
"""

        model_name = settings.GEMINI_MODEL or "gemini-2.5-flash"
        raw_text: Optional[str] = None
        diagnostic_data: Optional[DiagnosticOutput] = None

        # Call Gemini with bounded retries and timeout
        for retry in range(2):
            try:
                # Wrap synchronous GenAI client call in an asyncio executor with a 20s timeout
                loop = asyncio.get_running_loop()
                response = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                        ),
                    ),
                    timeout=20.0,
                )

                raw_text = getattr(response, "text", "") or str(response)
                # Parse JSON block
                clean_json_str = raw_text.strip()
                if clean_json_str.startswith("```json"):
                    clean_json_str = clean_json_str[7:]
                if clean_json_str.startswith("```"):
                    clean_json_str = clean_json_str[3:]
                if clean_json_str.endswith("```"):
                    clean_json_str = clean_json_str[:-3]

                parsed_json = json.loads(clean_json_str.strip())
                # Validate against Pydantic schema (untrusted data validation)
                diagnostic_data = DiagnosticOutput.model_validate(parsed_json)
                break
            except asyncio.TimeoutError:
                self.last_error = "Gemini API request timed out after 20 seconds."
                logger.warning(f"Timeout querying Gemini API for job {job.id} (attempt {retry + 1}/2)")
            except (json.JSONDecodeError, ValueError) as json_err:
                self.last_error = f"Malformed response received from Gemini API: {json_err}"
                logger.warning(f"Malformed response from Gemini API for job {job.id}: {json_err}")
                # Do not retry on malformed JSON; fail gracefully immediately
                break
            except Exception as exc:
                err_str = str(exc)
                if "RESOURCE_EXHAUSTED" in err_str or "429" in err_str:
                    self.last_error = "Gemini API rate limit or quota exceeded (429 RESOURCE_EXHAUSTED). Please check your Gemini API plan quota."
                elif "NOT_FOUND" in err_str or "404" in err_str or "model" in err_str.lower() and "not found" in err_str.lower():
                    self.last_error = f"Gemini model not found or unsupported ({model_name}): {err_str}"
                elif "API_KEY_INVALID" in err_str or "401" in err_str or "403" in err_str or "UNAUTHENTICATED" in err_str or "PERMISSION_DENIED" in err_str:
                    self.last_error = "Gemini API key is invalid or unauthorized (HTTP 401/403)."
                else:
                    self.last_error = f"Gemini API error: {err_str}"
                logger.warning(f"Error querying Gemini API for job {job.id} (attempt {retry + 1}/2): {exc}")
                await asyncio.sleep(1.0)

        if not diagnostic_data:
            return None

        # Persist diagnostic record in PostgreSQL
        diagnostic_record = FailureDiagnostic(
            job_id=job.id,
            attempt=attempt,
            model=model_name,
            error_category=diagnostic_data.error_category,
            likely_cause=diagnostic_data.likely_cause,
            confidence=diagnostic_data.confidence,
            recommended_next_steps=diagnostic_data.recommended_next_steps,
            retry_may_help=diagnostic_data.retry_may_help,
            reasoning_summary=diagnostic_data.reasoning_summary,
            raw_response={"raw_text": raw_text[:2000] if raw_text else None},
        )

        self.db.add(diagnostic_record)
        await self.db.commit()
        await self.db.refresh(diagnostic_record)

        logger.info(
            f"Persisted failure diagnostics for job {job.id} attempt {attempt} via {model_name}",
            extra={"job_id": str(job.id), "category": diagnostic_data.error_category},
        )
        return diagnostic_record
