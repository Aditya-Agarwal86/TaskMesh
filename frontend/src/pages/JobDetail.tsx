import React from 'react';
import { api, ApiError } from '../lib/api';
import { Job, JobEvent, FailureDiagnostic } from '../types/api';
import { StatusBadge } from '../components/StatusBadge';
import { PriorityTag } from '../components/PriorityTag';
import { JsonViewer } from '../components/JsonViewer';
import { GeminiDiagnosticsPanel } from '../components/GeminiDiagnosticsPanel';
import {
  ArrowLeft,
  RotateCw,
  Ban,
  Clock,
  ShieldAlert,
  Calendar,
  Copy,
  Check,
  AlertTriangle,
} from 'lucide-react';

interface JobDetailProps {
  jobId: string;
  onBack: () => void;
}

export const JobDetail: React.FC<JobDetailProps> = ({ jobId, onBack }) => {
  const [job, setJob] = React.useState<Job | null>(null);
  const [events, setEvents] = React.useState<JobEvent[]>([]);
  const [diagnostic, setDiagnostic] = React.useState<FailureDiagnostic | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [isLoadingDiag, setIsLoadingDiag] = React.useState(false);
  const [isAnalyzingDiag, setIsAnalyzingDiag] = React.useState(false);
  const [diagError, setDiagError] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [isActing, setIsActing] = React.useState(false);
  const [copied, setCopied] = React.useState(false);

  const fetchJobData = React.useCallback(async () => {
    try {
      setError(null);
      const [jobData, eventList] = await Promise.all([
        api.getJob(jobId),
        api.getJobEvents(jobId).catch(() => []),
      ]);
      setJob(jobData);
      setEvents(eventList);

      if (jobData.status === 'FAILED' || jobData.status === 'RETRYING' || jobData.error_message) {
        setIsLoadingDiag(true);
        api.getJobDiagnostics(jobId)
          .then((d) => setDiagnostic(d))
          .catch(() => {})
          .finally(() => setIsLoadingDiag(false));
      }
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 404) {
        setError(`Job ${jobId} was not found.`);
      } else {
        setError(err.message || 'Failed to load job details.');
      }
    } finally {
      setIsLoading(false);
    }
  }, [jobId]);

  const handleAnalyzeDiagnostic = async () => {
    setIsAnalyzingDiag(true);
    setDiagError(null);
    try {
      const res = await api.analyzeJobFailure(jobId);
      setDiagnostic(res);
    } catch (err: any) {
      setDiagError(err.message || 'Failed to trigger Gemini failure analysis');
    } finally {
      setIsAnalyzingDiag(false);
    }
  };

  React.useEffect(() => {
    fetchJobData();
    const interval = setInterval(fetchJobData, 4000);
    return () => clearInterval(interval);
  }, [fetchJobData]);

  const handleCopyId = () => {
    navigator.clipboard.writeText(jobId);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleCancel = async () => {
    if (!window.confirm('Are you sure you want to cancel this job?')) return;
    setIsActing(true);
    setActionError(null);
    try {
      const updated = await api.cancelJob(jobId, 'Cancelled from developer dashboard');
      setJob(updated);
      fetchJobData();
    } catch (err: any) {
      setActionError(err.message || 'Failed to cancel job');
    } finally {
      setIsActing(false);
    }
  };

  const handleRetry = async () => {
    setIsActing(true);
    setActionError(null);
    try {
      const updated = await api.retryJob(jobId);
      setJob(updated);
      fetchJobData();
    } catch (err: any) {
      setActionError(err.message || 'Failed to retry job');
    } finally {
      setIsActing(false);
    }
  };

  if (isLoading && !job) {
    return (
      <div className="loading-container">
        <Clock size={20} className="pulse-icon" />
        <span>Loading job metadata and state timeline...</span>
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="card">
        <div className="alert alert-error">
          <AlertTriangle size={18} />
          <div>{error || 'Job not found'}</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={onBack}>
          <ArrowLeft size={14} />
          <span>Back to Jobs</span>
        </button>
      </div>
    );
  }

  const canCancel = ['QUEUED', 'RUNNING', 'RETRYING'].includes(job.status);
  const canRetry = ['FAILED', 'CANCELLED'].includes(job.status);

  const formatDate = (iso?: string | null) => {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' ' + d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
      return iso;
    }
  };

  const computeDuration = () => {
    if (job.status === 'RUNNING' && job.started_at) {
      const elapsedMs = Math.max(0, Date.now() - new Date(job.started_at).getTime());
      return `${(elapsedMs / 1000).toFixed(1)}s (running)`;
    }
    if (job.started_at && job.completed_at) {
      const durationMs = Math.max(0, new Date(job.completed_at).getTime() - new Date(job.started_at).getTime());
      if (durationMs < 1000) {
        return `${durationMs}ms`;
      }
      return `${(durationMs / 1000).toFixed(2)}s`;
    }
    return null;
  };

  const durationStr = computeDuration();
  const hasActiveRetry = job.status === 'RETRYING' && !!job.next_retry_at;

  return (
    <div>
      {/* Top back navigation & action header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.25rem', flexWrap: 'wrap', gap: '0.75rem' }}>
        <button className="btn btn-secondary btn-sm" onClick={onBack}>
          <ArrowLeft size={14} />
          <span>Back</span>
        </button>

        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {canCancel && (
            <button className="btn btn-danger btn-sm" onClick={handleCancel} disabled={isActing}>
              <Ban size={14} />
              <span>Cancel Job</span>
            </button>
          )}

          {canRetry && (
            <button className="btn btn-primary btn-sm" onClick={handleRetry} disabled={isActing}>
              <RotateCw size={14} />
              <span>Retry Job</span>
            </button>
          )}
        </div>
      </div>

      {actionError && (
        <div className="alert alert-error">
          <AlertTriangle size={18} />
          <div>{actionError}</div>
        </div>
      )}

      {/* Main Metadata Card */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="card-header" style={{ alignItems: 'flex-start' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
              <h2 style={{ fontSize: '1.2rem', fontWeight: 700, color: '#fff' }}>
                {job.task_type}
              </h2>
              <StatusBadge status={job.status} />
              <PriorityTag priority={job.priority} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span className="mono-text" style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                {job.id}
              </span>
              <button className="btn btn-secondary btn-sm" onClick={handleCopyId} title="Copy ID" style={{ padding: '0.2rem 0.4rem' }}>
                {copied ? <Check size={12} /> : <Copy size={12} />}
              </button>
            </div>
          </div>

          <div style={{ textAlign: 'right' }}>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Attempts</span>
            <div className="mono-text" style={{ fontSize: '1.1rem', fontWeight: 700, color: '#fff' }}>
              {job.attempt_count} / {job.max_attempts}
            </div>
          </div>
        </div>

        {/* Timestamp & Worker grid */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--border-subtle)' }}>
          <div>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Created At</span>
            <div className="mono-text" style={{ fontSize: '0.8rem', color: '#fff' }}>{formatDate(job.created_at)}</div>
          </div>
          <div>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Execution Duration</span>
            <div className="mono-text" style={{ fontSize: '0.8rem', color: durationStr ? '#34d399' : '#94a3b8' }}>
              {durationStr || '—'}
            </div>
          </div>
          <div>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Completed At</span>
            <div className="mono-text" style={{ fontSize: '0.8rem', color: '#fff' }}>{formatDate(job.completed_at)}</div>
          </div>
          <div>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Next Scheduled Retry</span>
            <div className="mono-text" style={{ fontSize: '0.8rem', color: hasActiveRetry ? '#fbbf24' : '#94a3b8' }}>
              {hasActiveRetry ? formatDate(job.next_retry_at) : 'None scheduled'}
            </div>
          </div>
          <div>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Worker ID</span>
            <div className="mono-text" style={{ fontSize: '0.8rem', color: '#60a5fa' }}>{job.worker_id || '—'}</div>
          </div>
          <div>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Idempotency Key</span>
            <div className="mono-text" style={{ fontSize: '0.8rem', color: '#94a3b8' }}>{job.idempotency_key || '—'}</div>
          </div>
        </div>
      </div>

      {/* Failure Diagnostic Card (if failed or error recorded) */}
      {(job.error_message || job.error_type) && (
        <div className="card" style={{ marginBottom: '1.5rem', borderColor: 'rgba(248, 113, 113, 0.4)' }}>
          <div className="card-header">
            <div className="card-title" style={{ color: '#f87171' }}>
              <ShieldAlert size={17} />
              Structured Failure Information (System Facts)
            </div>
            {job.error_type && (
              <span className="mono-text" style={{ color: '#fca5a5', background: 'rgba(239, 68, 68, 0.1)', padding: '0.2rem 0.5rem', borderRadius: 4 }}>
                {job.error_type}
              </span>
            )}
          </div>
          <p style={{ color: '#f87171', fontSize: '0.9rem', marginBottom: job.error_details ? '0.75rem' : 0 }}>
            {job.error_message}
          </p>
          {job.error_details && <JsonViewer title="Error Details" data={job.error_details} />}
        </div>
      )}

      {/* Optional Gemini AI Failure Diagnostics Panel */}
      {(job.status === 'FAILED' || job.status === 'RETRYING' || job.error_message || job.error_type) && (
        <GeminiDiagnosticsPanel
          diagnostic={diagnostic}
          isLoading={isLoadingDiag}
          onAnalyze={handleAnalyzeDiagnostic}
          isAnalyzing={isAnalyzingDiag}
          error={diagError}
        />
      )}

      {/* Payloads & Results */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.5rem', marginBottom: '1.5rem' }}>
        <div className="card">
          <div className="card-header">
            <div className="card-title">Input Payload</div>
          </div>
          <JsonViewer data={job.payload} />
        </div>

        <div className="card">
          <div className="card-header">
            <div className="card-title">Output Result</div>
          </div>
          {job.result ? (
            <JsonViewer data={job.result} />
          ) : (
            <div className="empty-state" style={{ padding: '1rem' }}>
              <p style={{ color: 'var(--text-muted)' }}>
                {job.status === 'RUNNING'
                  ? 'Task execution in flight. Result pending completion...'
                  : job.status === 'QUEUED' || job.status === 'RETRYING'
                  ? 'Job waiting for worker execution.'
                  : job.status === 'FAILED'
                  ? 'Task encountered terminal error. See structured failure information above.'
                  : 'No output data recorded for this task.'}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* State-transition Event Timeline */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">
            <Calendar size={17} />
            State Transition Timeline ({events.length})
          </div>
        </div>

        {events.length === 0 ? (
          <div className="empty-state" style={{ padding: '1.5rem' }}>
            <p>No historical transition events recorded for this job.</p>
          </div>
        ) : (
          <div className="timeline">
            {events.map((evt) => (
              <div key={evt.id} className="timeline-item">
                <div className="timeline-marker" />
                <div className="timeline-title" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem' }}>
                  <span className="mono-text" style={{ color: '#fff', fontWeight: 600 }}>
                    {evt.event_type}
                  </span>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    {evt.metadata_payload?.attempt ? `Attempt #${evt.metadata_payload.attempt}` : ''}
                  </span>
                </div>
                <div className="timeline-time">{formatDate(evt.created_at)}</div>
                {evt.metadata_payload && Object.keys(evt.metadata_payload).length > 0 && (
                  <JsonViewer
                    title="Event Metadata"
                    data={evt.metadata_payload}
                    defaultCollapsed={true}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
