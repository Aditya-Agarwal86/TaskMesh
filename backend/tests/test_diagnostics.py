import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus, FailureDiagnostic
from app.services.diagnostics_service import (
    sanitize_data,
    GeminiDiagnosticsService,
)


def test_sanitize_data_redacts_sensitive_keys():
    raw_payload = {
        "job_name": "process_report",
        "api_key": "AIzaSySecret1234567890",
        "password": "supersecretpassword",
        "nested": {
            "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisSignature",
            "safe_counter": 42,
            "secret_url": "https://api.example.com?auth_token=abc",
        },
        "description": "normal text with Bearer secrettoken12345678 inside",
    }
    sanitized = sanitize_data(raw_payload)

    assert sanitized["job_name"] == "process_report"
    assert sanitized["api_key"] == "[REDACTED_SECRET]"
    assert sanitized["password"] == "[REDACTED_SECRET]"
    assert sanitized["nested"]["token"] == "[REDACTED_SECRET]"
    assert sanitized["nested"]["safe_counter"] == 42
    assert sanitized["nested"]["secret_url"] == "[REDACTED_SECRET]"
    assert "secrettoken12345678" not in sanitized["description"]
    assert "[REDACTED_TOKEN]" in sanitized["description"]


@pytest.mark.asyncio
async def test_diagnose_failure_when_unconfigured_returns_none(db_session: AsyncSession):
    job = Job(
        task_type="http_webhook",
        payload={"url": "https://example.com"},
        status=JobStatus.FAILED,
        error_message="Connection timed out after 30s",
        attempt_count=3,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with patch("app.services.diagnostics_service.settings.GEMINI_API_KEY", None):
        service = GeminiDiagnosticsService(db_session)
        result = await service.diagnose_job_failure(job.id)
        assert result is None


@pytest.mark.asyncio
async def test_diagnose_failure_success(db_session: AsyncSession):
    job = Job(
        task_type="data_processing",
        payload={"source": "s3://bucket/data.csv"},
        status=JobStatus.FAILED,
        error_message="FileNotFoundError: s3://bucket/data.csv does not exist",
        attempt_count=2,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    mock_response = MagicMock()
    mock_response.text = """```json
    {
        "error_category": "ResourceNotFound",
        "likely_cause": "The input file specified in the payload is missing or inaccessible in S3.",
        "confidence": "HIGH",
        "recommended_next_steps": [
            "Verify the S3 bucket path and filename",
            "Check IAM permissions for bucket access"
        ],
        "retry_may_help": false,
        "reasoning_summary": "FileNotFoundError indicates a static missing file condition which will not self-resolve upon retry."
    }
    ```"""

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    service = GeminiDiagnosticsService(db_session, client=mock_client)
    result = await service.diagnose_job_failure(job.id)

    assert result is not None
    assert result.job_id == job.id
    assert result.error_category == "ResourceNotFound"
    assert result.confidence == "HIGH"
    assert result.retry_may_help is False
    assert len(result.recommended_next_steps) == 2
    assert "S3 bucket" in result.recommended_next_steps[0]

    # Verify DB persistence
    from sqlalchemy import select
    saved = (await db_session.execute(
        select(FailureDiagnostic).where(FailureDiagnostic.job_id == job.id)
    )).scalars().all()
    assert len(saved) == 1
    assert saved[0].error_category == "ResourceNotFound"


@pytest.mark.asyncio
async def test_diagnose_failure_malformed_json_fallback(db_session: AsyncSession):
    job = Job(
        task_type="report_generator",
        payload={},
        status=JobStatus.FAILED,
        error_message="SyntaxError in custom script",
        attempt_count=1,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    mock_response = MagicMock()
    # Malformed / not JSON
    mock_response.text = "I am an AI assistant and I think the issue is a syntax error."

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    service = GeminiDiagnosticsService(db_session, client=mock_client)
    result = await service.diagnose_job_failure(job.id)
    assert result is None


@pytest.mark.asyncio
async def test_diagnose_failure_api_timeout(db_session: AsyncSession):
    job = Job(
        task_type="webhook",
        payload={},
        status=JobStatus.FAILED,
        error_message="504 Gateway Timeout",
        attempt_count=1,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    mock_client = MagicMock()
    # Simulate a timeout when generate_content runs
    def raise_timeout(*args, **kwargs):
        raise TimeoutError("Request timed out")

    mock_client.models.generate_content.side_effect = raise_timeout

    service = GeminiDiagnosticsService(db_session, client=mock_client)
    result = await service.diagnose_job_failure(job.id)
    assert result is None


@pytest.mark.asyncio
async def test_diagnose_failure_quota_error(db_session: AsyncSession):
    job = Job(
        task_type="webhook",
        payload={},
        status=JobStatus.FAILED,
        error_message="500 Internal Error",
        attempt_count=1,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    mock_client = MagicMock()
    def raise_quota(*args, **kwargs):
        raise Exception("429 ResourceExhausted: Quota exceeded")

    mock_client.models.generate_content.side_effect = raise_quota

    service = GeminiDiagnosticsService(db_session, client=mock_client)
    result = await service.diagnose_job_failure(job.id)
    assert result is None


@pytest.mark.asyncio
async def test_diagnostics_endpoint_trigger_and_retrieve(client, db_session: AsyncSession):
    job = Job(
        task_type="http_request",
        payload={"url": "https://api.stripe.com/v1/charges"},
        status=JobStatus.FAILED,
        error_message="StripeAuthenticationError: Invalid API Key provided",
        attempt_count=1,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    mock_response = MagicMock()
    mock_response.text = """```json
    {
        "error_category": "AuthenticationFailure",
        "likely_cause": "The Stripe secret key provided is either expired, malformed, or missing.",
        "confidence": "HIGH",
        "recommended_next_steps": [
            "Check the environment configuration for STRIPE_SECRET_KEY",
            "Rotate the API key in the Stripe dashboard"
        ],
        "retry_may_help": false,
        "reasoning_summary": "Authentication errors do not resolve with backoff retries."
    }
    ```"""

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    # Patch genai.Client in diagnostics_service so when endpoint instantiates GeminiDiagnosticsService it gets mock_client
    with patch("app.services.diagnostics_service.settings.GEMINI_API_KEY", "mock-gemini-key"), \
         patch("app.services.diagnostics_service.settings.GEMINI_MODEL", "gemini-2.5-flash"), \
         patch("app.services.diagnostics_service.genai.Client", return_value=mock_client):

        # 1. Trigger analysis via POST
        res = await client.post(f"/api/v1/jobs/{job.id}/diagnostics/analyze")
        assert res.status_code == 200
        data = res.json()
        assert data["error_category"] == "AuthenticationFailure"
        assert data["retry_may_help"] is False
        assert data["model"] == "gemini-2.5-flash"

        # 2. Retrieve history via GET
        get_res = await client.get(f"/api/v1/jobs/{job.id}/diagnostics")
        assert get_res.status_code == 200
        diag = get_res.json()
        assert diag is not None
        assert diag["error_category"] == "AuthenticationFailure"
        assert "STRIPE_SECRET_KEY" in diag["recommended_next_steps"][0]


@pytest.mark.asyncio
async def test_diagnose_failure_model_not_found(db_session: AsyncSession):
    """Verifies that 404 / model not found errors are gracefully handled without crashing."""
    job = Job(
        task_type="webhook",
        payload={},
        status=JobStatus.FAILED,
        error_message="502 Bad Gateway",
        attempt_count=1,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("404 NOT_FOUND: models/gemini-unsupported-model is not found")

    service = GeminiDiagnosticsService(db_session, client=mock_client)
    result = await service.diagnose_job_failure(job.id)
    assert result is None
    assert "not found" in service.last_error.lower()


@pytest.mark.asyncio
async def test_diagnose_failure_authentication_error(db_session: AsyncSession):
    """Verifies that 401/403 or invalid API key errors are categorized correctly."""
    job = Job(
        task_type="webhook",
        payload={},
        status=JobStatus.FAILED,
        error_message="Connection Refused",
        attempt_count=1,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("401 API_KEY_INVALID: API key not valid")

    service = GeminiDiagnosticsService(db_session, client=mock_client)
    result = await service.diagnose_job_failure(job.id)
    assert result is None
    assert "invalid or unauthorized" in service.last_error.lower()


@pytest.mark.asyncio
async def test_diagnostics_endpoint_unconfigured_returns_503(client, db_session: AsyncSession):
    """Verifies that requesting /analyze when GEMINI_API_KEY is not configured returns 503 DIAGNOSTICS_UNAVAILABLE."""
    job = Job(
        task_type="http_webhook",
        payload={"url": "https://example.com"},
        status=JobStatus.FAILED,
        error_message="Connection timed out",
        attempt_count=2,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with patch("app.services.diagnostics_service.settings.GEMINI_API_KEY", None):
        res = await client.post(f"/api/v1/jobs/{job.id}/diagnostics/analyze")
        assert res.status_code == 503
        data = res.json()
        assert data["detail"]["code"] == "DIAGNOSTICS_UNAVAILABLE"
        assert "GEMINI_API_KEY" in data["detail"]["message"]

