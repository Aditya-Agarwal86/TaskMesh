# TaskMesh Developer Guide & Startup Manual

TaskMesh is a resilient distributed task processing platform built with FastAPI, PostgreSQL transactional outbox, Redis-backed priority queue, Python worker engine, and a React + TypeScript developer dashboard.

---

## Architecture Summary

- **Backend API**: FastAPI on `http://localhost:8000` with Swagger docs at `/docs`, SSE event stream at `/api/v1/events/stream`, and comprehensive metrics at `/health/metrics`.
- **Database & Outbox**: PostgreSQL with transactional outbox (`outbox_events`) ensuring atomic consistency on job creation and audit logging.
- **Priority Queue**: Redis sorted set (`ZSET`) with scores (CRITICAL=10, HIGH=8, NORMAL=5, LOW=1) and atomic Lua claim/lease scripts.
- **Real-Time Observability**: Server-Sent Events (SSE) with bounded subscriber queues (100 cap per client), 15s heartbeat pings, Redis Pub/Sub notification bridge across pods, and automatic polling fallback on client disconnection.
- **Workers**: Python async worker processes with lease renewal heartbeats and optimistic concurrency fencing tokens.
- **Frontend Dashboard**: React 18, TypeScript, and Vite on `http://localhost:5173`.


---

## Startup Instructions

### 1. Prerequisites
Ensure PostgreSQL and Redis servers are running locally:
- PostgreSQL default: `localhost:5432` (database `taskmesh`)
- Redis default: `localhost:6379`

### 2. Run Database Migrations
From the project root:
```bash
python -m alembic upgrade head
```

### 3. Start the FastAPI Backend
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
API endpoints will be live at `http://localhost:8000`.

### 4. Start the Outbox Dispatcher & Workers
In separate terminal windows:
```bash
# Start outbox dispatcher (polls outbox_events and publishes to Redis)
python -m app.workers.dispatcher

# Start a worker process (claims jobs by priority, runs handlers, renews leases)
python -m app.workers.engine --worker-id worker-alpha-1 --concurrency 4
```

### 5. Start the React Frontend Dashboard
Navigate to the `frontend/` directory:
```bash
cd frontend
npm install
npm run dev
```
Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## Frontend Test Suite
To run frontend component and flow tests with Vitest:
```bash
cd frontend
npm test
```

To build production bundle:
```bash
cd frontend
npm run build
```

---

## Backend Test Suite
To execute all backend unit, integration, and lifecycle tests:
```bash
python -m pytest backend/tests -v
```

---

## Features Implemented in the Frontend Dashboard

1. **Overview Dashboard**
   - Live telemetry: Total jobs, Queued, Running, Retrying, Succeeded, Failed, Cancelled.
   - Redis Queue depth breakdown: Pending priority queue, Active processing leases, and DLQ.
   - Reliability progress bar (Succeeded vs. Permanent Failures).
   - Recent jobs table with instant click-through to detail view.
   - Recent system errors feed.

2. **Job Explorer**
   - Paginated job table with sortable columns and custom page sizes.
   - Search by job UUID.
   - Multi-field filters: Status, Priority (CRITICAL, HIGH, NORMAL, LOW), Task Type.
   - Filter clear button and clickable rows.

3. **Job Submission**
   - Preset buttons for registered task types (`sleep`, `generate_report`, `fail_once`, `always_fail`) or Custom task type.
   - Formatted, syntax-validated JSON payload editor.
   - Priority selection and maximum attempts with backoff jitter support.
   - Optional Idempotency key for deduplication and replay prevention.
   - Inline API validation errors and success confirmation card with direct link to job details.

4. **Job Details**
   - Comprehensive metadata: Status badge, priority tag, attempt counter, ISO timestamps (created, started, completed, next scheduled retry).
   - Active worker ID and lease expiration status.
   - Formatted JSON viewers for Input payload and Output result.
   - Structured Failure Information diagnostic card displaying error type and error details.
   - Chronological state-transition audit event timeline with transition payloads.
   - Context-aware action buttons: **Cancel Job** (for queued/running/retrying jobs) and **Retry Job** (for failed/cancelled jobs).

5. **Workers & Queue Telemetry**
   - Live infrastructure health probes (PostgreSQL, Redis, Dispatcher loop).
   - Active worker table listing worker IDs, busy status, claimed job IDs, and lease expiration timestamps.
   - Real-time in-flight jobs table.
   - Priority queue policy and atomic Lua lease documentation.

6. **Backend Integration & Reliability**
   - Centralized typed API client with configurable base URL (`VITE_API_URL`).
   - Resilient bounded polling with manual refresh toggle and interval cleanup on unmount.
   - Uniform error code and message handling for timeouts, network drops, and HTTP 4xx/5xx responses.

---

## Optional Gemini Failure Diagnostics

TaskMesh includes an optional, zero-dependency AI diagnostics feature powered by the official Google Gen AI SDK (`google-genai`). When configured, TaskMesh synthesizes structured root-cause analyses and remediation steps for failed or retrying jobs.

### Configuration
Configure your Gemini API key and model in `.env` or system environment:
```bash
# Optional: Google Gemini API Key
GEMINI_API_KEY="AIzaSyYourGeminiApiKeyHere"

# Optional: Configurable model (defaults to gemini-2.5-flash)
GEMINI_MODEL="gemini-2.5-flash"
```

If `GEMINI_API_KEY` is omitted or left blank:
- The core queue, worker engine, dispatcher, and dashboard continue to function at 100% capacity without error.
- Failure diagnostics are gracefully disabled or return a 503 error if explicitly invoked via the API.

### Security & Safety Rules
1. **Confidentiality & Secret Redaction**: Before telemetry is sent to Gemini, payloads and error details are sanitized by stripping secrets, API keys, passwords, JWTs, and Authorization bearer headers.
2. **Untrusted Model Output**: Diagnostics outputs are strictly parsed and validated against a Pydantic schema (`DiagnosticOutput`). Model output is treated as untrusted data and never executed as code or commands.
3. **Deterministic Safety Rules**: AI recommendations never override retry limits, max attempts, worker fencing tokens, or cancellation states.
4. **Resilience**: The client uses bounded retries and an asynchronous executor with a 12-second timeout to prevent stalling the server.
5. **Clear UI Separation**: The dashboard displays AI diagnostics inside a dedicated purple card with a prominent advisory disclaimer badge distinguishing AI hypotheses from verified PostgreSQL system telemetry.
