export type JobStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'RETRYING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED';

export type JobPriority = 1 | 5 | 8 | 10;

export interface Job {
  id: string;
  task_type: string;
  payload: Record<string, any>;
  result?: Record<string, any> | null;
  priority: number;
  status: JobStatus;
  attempt_count: number;
  max_attempts: number;
  execution_cycle?: number;
  total_attempt_count?: number;
  idempotency_key?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  next_retry_at?: string | null;
  worker_id?: string | null;
  lease_expires_at?: string | null;
  error_type?: string | null;
  error_message?: string | null;
  error_details?: Record<string, any> | null;
}

export interface JobEvent {
  id: string;
  job_id: string;
  event_type: string;
  metadata_payload?: Record<string, any> | null;
  created_at: string;
}

export interface PaginatedJobsResponse {
  items: Job[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface JobCreateRequest {
  task_type: string;
  payload?: Record<string, any>;
  priority?: number;
  max_attempts?: number;
  idempotency_key?: string;
}

export interface WorkerHeartbeatInfo {
  worker_id: string;
  last_heartbeat: string;
  status: string;
  current_job_id?: string | null;
  lease_expires_at?: string | null;
}

export interface ExecutionDurationMetrics {
  avg_seconds: number;
  min_seconds: number;
  max_seconds: number;
  sample_count: number;
}

export interface SystemMetrics {
  queue_depth: {
    pending: number;
    processing: number;
    dlq: number;
  };
  job_counts_by_status: Record<JobStatus, number>;
  service_status: string;
  total_jobs: number;
  processed_jobs: number;
  succeeded_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  cancelled_jobs?: number;
  terminal_jobs: number;
  total_retries: number;
  active_workers_count: number;
  workers: WorkerHeartbeatInfo[];
  execution_duration: ExecutionDurationMetrics;
}

export interface SSEJobUpdateData {
  job_id: string;
  status: JobStatus;
  task_type?: string;
  worker_id?: string;
  attempt?: number;
  event_type?: string;
  duration_s?: number;
}


export interface HealthResponse {
  status: string;
  service: string;
  environment: string;
}

export interface ReadyResponse {
  status: string;
  database: string;
  redis: string;
}

export interface FailureDiagnostic {
  id: string;
  job_id: string;
  attempt: number;
  model: string;
  error_category: string;
  likely_cause: string;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW' | string;
  recommended_next_steps: string[];
  retry_may_help: boolean;
  reasoning_summary: string;
  created_at: string;
}
