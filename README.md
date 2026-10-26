# TaskMesh Developer Guide & Operations Manual

TaskMesh is a resilient distributed task processing platform built with FastAPI, PostgreSQL (authoritative source of truth with transactional outbox), Redis (sorted-set priority queues, atomic Lua leases, and pub/sub), Python asynchronous worker engines, and a React 18 + TypeScript developer dashboard.

---

## Architecture & Data Flow

```mermaid
flowchart TD
    Client["React 18 Dashboard<br/>(TypeScript / Vite / SSE)"]
    API["FastAPI API Pod<br/>(Input Validation / Registry)"]
    PG[("PostgreSQL 16 Database<br/>(Jobs + Outbox + Events)")]
    Dispatcher["Outbox Dispatcher<br/>(FOR UPDATE SKIP LOCKED)"]
    Redis[("Redis 7 Instance<br/>(Priority ZSET + Leases + PubSub)")]
    Workers["Worker Engine Pods<br/>(Lua Claims / Heartbeats / Handlers)"]

    Client -->|"HTTP POST / REST"| API
    API -->|"Atomic ACID Commit"| PG
    Dispatcher -->|"Poll Pending Outbox"| PG
    Dispatcher -->|"ZADD Priority Score"| Redis
    Redis -->|"Atomic Lua Claim / Lease"| Workers
    Workers -->|"State Transitions & Events"| PG
    Workers -->|"Heartbeat TTL Renewal"| Redis
    Redis -->|"Pub/Sub Event Notifications"| API
    API -->|"SSE Real-Time Stream"| Client
```

### 1. Data Flow Walkthrough
1. **Job Submission**: The client submits a task via `POST /api/v1/jobs`. The API validates the `task_type` against the authoritative server task registry. If invalid, it returns `HTTP 422 Unprocessable Content` with registered options and aborts before touching storage.
2. **Atomic Persistence**: In a single PostgreSQL transaction with row locks, the API writes:
   - The `Job` record with state `QUEUED` and `execution_cycle = 1`.
   - An initial `JobEvent` record (`JOB_CREATED`).
   - An `OutboxEvent` record (`JOB_QUEUED`).
3. **Outbox Dispatcher**: The standalone dispatcher polls `outbox_events` with `SELECT ... FOR UPDATE SKIP LOCKED`, pushes tasks into the Redis priority `ZSET`, updates outbox status to `PROCESSED`, and publishes a notification on Redis pub/sub.
4. **Worker Lease Acquisition**: Workers poll Redis using an atomic Lua script (`claim_task.lua`) that moves jobs from `pending` to `processing` and sets a heartbeat lease key (`job:lease:<id>`) with an incremented `fencing_token`.
5. **Execution & Cooperative Cancellation**: The worker runs the registered task handler. Long-running tasks query `await context.is_cancelled()` at checkpoints. If cancellation was requested, the task safely aborts.
6. **Completion or Retry**:
   - On success: State transitions to `SUCCEEDED` in PostgreSQL, `JOB_COMPLETED` audit event is written, and Redis ack removes the lease.
   - On failure: If attempts remain within the current cycle, state transitions to `RETRYING` and exponential backoff schedules the next attempt. If attempts are exhausted, the job transitions to `FAILED` (terminal dead-letter state).
7. **Manual Retries**: Retrying a terminally failed job starts a **new execution cycle** (`execution_cycle + 1`), resets current-cycle attempts to 0, and preserves complete historical attempts.

---

## Local Setup & Quickstart

### Prerequisites
- Docker & Docker Compose (recommended) OR:
- Python 3.12+
- Node.js 18+ & npm
- PostgreSQL 16+
- Redis 7+

### A. Quickstart with Docker Compose (Recommended)
Clone the repository and launch the full stack:
```bash
# 1. Clone repository
git clone https://github.com/Aditya-Agarwal86/TaskMesh.git
cd TaskMesh

# 2. Launch containerized services
docker-compose up -d --build
```
Containers started:
- `taskmesh-postgres` (Port 5432)
- `taskmesh-redis` (Port 6379)
- `taskmesh-api` (Port 8000)
- `taskmesh-dispatcher`
- `taskmesh-worker-1`
- `taskmesh-worker-2`
- `taskmesh-frontend` (Port 5173)

### B. Manual Local Setup

1. **Environment Configuration**:
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. **Database Migrations**:
   Run Alembic migrations to apply PostgreSQL schemas:
   ```bash
   cd backend
   python -m alembic upgrade head
   ```

3. **Start Backend API**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

4. **Start Dispatcher & Worker**:
   In separate terminals:
   ```bash
   # Dispatcher loop
   python -m app.workers.dispatcher

   # Worker engine process
   python -m app.workers.engine --worker-id worker-local-1 --concurrency 4
   ```

5. **Start Frontend Client**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
   Open `http://localhost:5173`.

---

## Environment Configuration

Safe configuration template (`.env.example`):

```bash
# Server & Environment
PROJECT_NAME="TaskMesh"
ENVIRONMENT="development"
DEBUG="True"
API_V1_PREFIX="/api/v1"

# Database Configuration (PostgreSQL)
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/taskmesh"
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20

# Redis Configuration
REDIS_URL="redis://localhost:6379/0"

# Worker & Queue Tuning
WORKER_CONCURRENCY=4
LEASE_DURATION_SECONDS=60
LEASE_HEARTBEAT_INTERVAL=15
DISPATCHER_POLL_INTERVAL=1.0

# CORS Allowed Origins
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]

# Optional: Google Gemini AI Diagnostics
# Obtain API key from Google AI Studio (leave blank to run in fully offline mode)
GEMINI_API_KEY=""
GEMINI_MODEL="gemini-2.5-flash"
```

---

## Supported Task Types & Example Payloads

TaskMesh rejects unregistered tasks at the API boundary with `HTTP 422`. The authoritative task registry supports:

| Task Type | Description | Example Payload |
| :--- | :--- | :--- |
| `sleep` | Pauses execution with cooperative cancellation checkpoints. | `{"duration_seconds": 1.5, "checkpoint_interval": 0.5}` |
| `generate_report` | Computes deterministic report data hashing. | `{"report_name": "quarterly_summary", "format": "summary", "dataset": [{"count": 10}]}` |
| `email_notification`| Simulates queued transactional email delivery. | `{"to": "dev@taskmesh.internal", "template": "alert"}` |
| `invoice_generation`| Renders deterministic customer invoice reference. | `{"order_id": 1042}` |
| `pdf_render` | Simulates multi-page document artifact generation. | `{"document_id": "doc-report-2026"}` |
| `data_sync` | Synchronizes records in batches to simulate ETL ingestion. | `{"records_count": 50}` |
| `fail_once` | Fails on attempt 1, succeeds on automatic retry via state. | `{"key": "test-key"}` |
| `always_fail` | Permanently fails to test DLQ & failure diagnostics. | `{"reason": "Simulated hardware error"}` |

---

## API Endpoints Reference

### Core Endpoints

- `GET /health/live`: Liveness probe for load balancers.
- `GET /health/ready`: Readiness check verifying PostgreSQL and Redis connections.
- `GET /health/metrics`: System telemetry (queue depths, status counts, duration stats).
- `GET /api/v1/events/stream`: SSE real-time event stream with heartbeat ping.
- `POST /api/v1/jobs`: Submit a new job (Accepts `task_type`, `payload`, `priority`, `max_attempts`, `idempotency_key`). Returns `202 Accepted` or `422 Unprocessable Content`.
- `GET /api/v1/jobs`: Paginated listing with server-side filters (`status`, `task_type`, `priority`).
- `GET /api/v1/jobs/{job_id}`: Job details, execution counters, and error tracebacks.
- `GET /api/v1/jobs/{job_id}/events`: Chronological state-transition audit log.
- `POST /api/v1/jobs/{job_id}/cancel`: Cancel a queued, running, or retrying job.
- `POST /api/v1/jobs/{job_id}/retry`: Manually retry a `FAILED` or `CANCELLED` job into a new execution cycle.
- `GET /api/v1/jobs/{job_id}/diagnostics`: Retrieve stored Gemini failure analysis.
- `POST /api/v1/jobs/{job_id}/diagnostics/analyze`: Request on-demand Gemini failure analysis.

---

## Reliability & State Machine Semantics

1. **Cycle Limits vs. Lifetime Attempts**:
   - `max_attempts`: Maximum automatic retries permitted **within a single execution cycle**.
   - `attempt_count`: Attempts consumed in the current cycle.
   - `execution_cycle`: Incremented on each manual retry.
   - `total_executions`: Cumulative lifetime executions across all cycles.
2. **Idempotency & Concurrent Submissions**:
   - Submissions with an `idempotency_key` store the key in PostgreSQL.
   - Duplicate submissions with identical keys and payloads return the existing job.
   - Conflicting submissions with the same key but differing payloads return `HTTP 409 Conflict`.
3. **Leasing & Fencing**:
   - Leases expire after 60 seconds if heartbeat fails.
   - Zombie workers attempting to commit after supervisor lease reassignment fail the fencing token check (`fencing_token` mismatch) and abort their write.
4. **Cooperative Cancellation**:
   - Running jobs check cancellation signals in Redis via `await context.is_cancelled()`.
   - CPU-bound tasks without checkpoints run until task completion or lease expiration.
5. **Dead-Letter Safety**:
   - Terminal jobs (`SUCCEEDED`) reject `/retry` requests with `HTTP 409 Conflict` to prevent duplicate processing.

---

## Optional Gemini AI Failure Diagnostics

TaskMesh includes optional root-cause analysis powered by Google Gemini:
- **Upstream Model**: Verified against current official documentation using **`gemini-2.5-flash`** (or configured via `GEMINI_MODEL`).
- **Sanitization**: All secrets, JWTs, Bearer tokens, and sensitive keys are recursively redacted (`[REDACTED_SECRET]`) before prompt generation.
- **Strictly Advisory**: AI output is purely advisory developer assistance. It **never** triggers retries, alters max attempts, overrides worker fencing, or sets authoritative job status.
- **Graceful Failure**: If `GEMINI_API_KEY` is not provided, or if the API returns 429 quota exhaustion or timeouts, the error is disclosed truthfully and deterministic error details remain accessible.

---

## Testing & Quality Verification

### Run Backend Tests
Run all unit and integration tests inside Docker or local virtual environment:
```bash
docker exec taskmesh-api python -m pytest -v
# Output: 58 passed across 8 test suites
```

### Run Frontend Tests
```bash
cd frontend
npm test -- --watchAll=false
# Output: 19 passed across 6 test suites
```

### Build Frontend Production Assets
```bash
cd frontend
npm run build
# Output: 0 type/bundle errors, generated in dist/
```

---

## Troubleshooting Guide

| Issue | Cause | Resolution |
| :--- | :--- | :--- |
| **Worker not processing jobs** | Redis connection down or queue dispatcher stopped | Check `docker ps` to verify `taskmesh-dispatcher` is running; inspect Redis connectivity via `redis-cli ping`. |
| **SSE shows "Reconnecting"** | Backend restarted or reverse proxy dropping streaming connections | Verify CORS origin configuration in `.env`; TaskMesh automatically falls back to 4s polling. |
| **Job stuck in RUNNING** | Worker process crashed without clean shutdown | Wait for lease expiration (default 60s); supervisor automatically reclaims the job and transitions it to `RETRYING`. |
| **HTTP 422 on job submission** | Task type not in server task registry | Check supported task types via preset dropdown or review `TaskRegistry.get_supported_tasks()`. |
| **Gemini 503 DIAGNOSTICS_UNAVAILABLE** | Missing `GEMINI_API_KEY` or rate limit | Ensure valid key is set in `.env` or examine quota in Google AI Studio. System functions normally without AI key. |

---

## Security Model & Deployment Checklist

### Current Security Posture
- **Target Context**: Currently configured for a **trusted local development and portfolio demonstration environment**.
- **CORS Protection**: Bound strictly to configured frontend development origins (`http://localhost:5173`, `http://127.0.0.1:5173`). Wildcard `*` origins are disabled.
- **Secret Redaction**: Payloads sent to external providers (e.g. Gemini) recursively scrub passwords, tokens, API keys, JWTs, and bearer strings (`[REDACTED_SECRET]`).
- **Input Bounds**: Pydantic schemas enforce type validation, priority boundaries (1-10), attempt limits, and supported task types at the API perimeter.
- **Zero Raw Traces**: Client errors return clean structured codes (`VALIDATION_ERROR`, `JOB_NOT_FOUND`, `INVALID_STATE_FOR_RETRY`) rather than internal database schemas or unhandled exceptions.

### Known Security & Operational Limitations (Before Public Staging/Production)
1. **Authentication & RBAC**: The API currently does not require authentication headers or role checks for job submission, cancellation, or retries. Before deploying to an untrusted public network, an API gateway or OAuth2/JWT middleware must be configured.
2. **Container Non-Root User**: Docker containers currently run as default root. For staging Kubernetes/ECS deployments, specify `USER 1000:1000` in the Dockerfile.
3. **Database High Availability**: PostgreSQL and Redis run as standalone Docker Compose services. Production requires managed multi-AZ instances with continuous automated WAL archiving and Redis Sentinel/Cluster.
4. **Rate Limiting**: Add reverse proxy rate limiting (e.g. Nginx or Cloudflare) to prevent DoS attacks on `/api/v1/jobs` and `/events/stream`.

