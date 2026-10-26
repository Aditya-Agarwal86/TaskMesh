import React from 'react';
import { api, ApiError } from '../lib/api';
import { SystemMetrics, Job } from '../types/api';
import { MetricCard } from '../components/MetricCard';
import { JobTable } from '../components/JobTable';
import {
  Layers,
  Clock,
  Play,
  RotateCw,
  CheckCircle,
  XCircle,
  Ban,
  Activity,
  AlertTriangle,
  ArrowRight,
} from 'lucide-react';

interface OverviewProps {
  onSelectJob: (job: Job) => void;
  onNavigateExplorer: (filterStatus?: string) => void;
  refreshTrigger?: number;
}

export const Overview: React.FC<OverviewProps> = ({ onSelectJob, onNavigateExplorer, refreshTrigger }) => {
  const [metrics, setMetrics] = React.useState<SystemMetrics | null>(null);
  const [recentJobs, setRecentJobs] = React.useState<Job[]>([]);
  const [failedJobs, setFailedJobs] = React.useState<Job[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const fetchOverviewData = React.useCallback(async (silent = false) => {
    try {
      if (!silent) {
        setError(null);
      }
      const [m, jobsRes, errorJobsRes] = await Promise.all([
        api.getMetrics(),
        api.listJobs({ page: 1, page_size: 6 }),
        api.listJobs({ status: 'FAILED', page: 1, page_size: 10 }),
      ]);

      setMetrics(m);
      setRecentJobs(jobsRes.items || []);
      setFailedJobs(errorJobsRes.items || []);
    } catch (err: any) {
      if (!silent) {
        if (err instanceof ApiError) {
          setError(`Failed to load metrics: ${err.message}`);
        } else {
          setError('Error loading system overview data.');
        }
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    fetchOverviewData();
    const interval = setInterval(() => fetchOverviewData(true), 5000);
    return () => clearInterval(interval);
  }, [fetchOverviewData]);

  // Refresh in-place when SSE or manual refresh fires without unmounting or showing full screen loading
  React.useEffect(() => {
    if (refreshTrigger !== undefined && refreshTrigger > 0) {
      fetchOverviewData(true);
    }
  }, [refreshTrigger, fetchOverviewData]);

  if (isLoading && !metrics) {
    return (
      <div className="loading-container">
        <Activity size={20} className="pulse-icon" />
        <span>Loading live system telemetry...</span>
      </div>
    );
  }

  const counts = metrics?.job_counts_by_status || {
    QUEUED: 0,
    RUNNING: 0,
    RETRYING: 0,
    SUCCEEDED: 0,
    FAILED: 0,
    CANCELLED: 0,
  };

  const totalJobs = Object.values(counts).reduce((a, b) => a + b, 0);
  const queueDepth = metrics?.queue_depth || { pending: 0, processing: 0, dlq: 0 };
  const successRate =
    counts.SUCCEEDED + counts.FAILED > 0
      ? Math.round((counts.SUCCEEDED / (counts.SUCCEEDED + counts.FAILED)) * 100)
      : 100;

  // Format execution duration cleanly: use ms when under 1.0s
  const formatDurationMetric = (sec: number) => {
    if (sec <= 0) return '0s';
    if (sec < 1.0) {
      return `${Math.round(sec * 1000)}ms`;
    }
    return `${sec.toFixed(2)}s`;
  };

  // Group failures by error type or message
  const failureTypeCounts: Record<string, number> = {};
  for (const fj of failedJobs) {
    const reason = fj.error_message?.split(':')[0]?.substring(0, 45) || fj.task_type || 'Unknown Error';
    failureTypeCounts[reason] = (failureTypeCounts[reason] || 0) + 1;
  }

  return (
    <div>
      {error && (
        <div className="alert alert-error">
          <AlertTriangle size={18} />
          <div>{error}</div>
        </div>
      )}

      {/* Metric Cards Row 1: High level counts with clickable navigation to filtered Job Explorer */}
      <div className="metrics-grid">
        <div onClick={() => onNavigateExplorer()} style={{ cursor: 'pointer' }}>
          <MetricCard
            label="Total Jobs"
            value={metrics?.total_jobs ?? totalJobs}
            subtitle={`${metrics?.processed_jobs ?? (counts.SUCCEEDED + counts.FAILED + counts.RUNNING + counts.RETRYING)} processed`}
            icon={<Layers size={18} />}
          />
        </div>
        <div onClick={() => onNavigateExplorer('QUEUED')} style={{ cursor: 'pointer' }}>
          <MetricCard
            label="Queued"
            value={counts.QUEUED}
            subtitle={`Pending dispatch: ${queueDepth.pending}`}
            icon={<Clock size={18} />}
          />
        </div>
        <div onClick={() => onNavigateExplorer('RUNNING')} style={{ cursor: 'pointer' }}>
          <MetricCard
            label="Running"
            value={counts.RUNNING}
            subtitle={`Active leases: ${queueDepth.processing}`}
            icon={<Play size={18} />}
          />
        </div>
        <div onClick={() => onNavigateExplorer('RETRYING')} style={{ cursor: 'pointer' }}>
          <MetricCard
            label="Retrying"
            value={counts.RETRYING}
            subtitle="Awaiting backoff jitter"
            icon={<RotateCw size={18} />}
          />
        </div>
        <div onClick={() => onNavigateExplorer('SUCCEEDED')} style={{ cursor: 'pointer' }}>
          <MetricCard
            label="Succeeded"
            value={counts.SUCCEEDED}
            subtitle={`${successRate}% completed rate`}
            icon={<CheckCircle size={18} />}
          />
        </div>
        <div onClick={() => onNavigateExplorer('FAILED')} style={{ cursor: 'pointer' }}>
          <MetricCard
            label="Failed"
            value={counts.FAILED}
            subtitle={`Dead letter queue: ${queueDepth.dlq}`}
            icon={<XCircle size={18} />}
          />
        </div>
        <div onClick={() => onNavigateExplorer('CANCELLED')} style={{ cursor: 'pointer' }}>
          <MetricCard
            label="Cancelled"
            value={counts.CANCELLED}
            subtitle="Aborted by operator"
            icon={<Ban size={18} />}
          />
        </div>
      </div>

      {/* Queue breakdown & Trends */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.5rem', marginBottom: '1.5rem' }}>
        <div className="card">
          <div className="card-header">
            <div className="card-title">
              <Activity size={17} style={{ color: 'var(--accent-blue)' }} />
              Queue Depth Breakdown
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
            <div
              style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
              onClick={() => onNavigateExplorer('QUEUED')}
              title="Click to view queued jobs"
            >
              <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Redis Pending Priority Queue:</span>
              <span className="mono-text" style={{ fontSize: '0.95rem', fontWeight: 600, color: '#fff', textDecoration: 'underline' }}>
                {queueDepth.pending}
              </span>
            </div>
            <div
              style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
              onClick={() => onNavigateExplorer('RUNNING')}
              title="Click to view running jobs"
            >
              <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Processing Leases:</span>
              <span className="mono-text" style={{ fontSize: '0.95rem', fontWeight: 600, color: '#60a5fa', textDecoration: 'underline' }}>
                {queueDepth.processing}
              </span>
            </div>
            <div
              style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
              onClick={() => onNavigateExplorer('FAILED')}
              title="Click to view failed jobs"
            >
              <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Dead-Letter Queue (DLQ):</span>
              <span className="mono-text" style={{ fontSize: '0.95rem', fontWeight: 600, color: '#f87171', textDecoration: 'underline' }}>
                {queueDepth.dlq}
              </span>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <div className="card-title">
              <CheckCircle size={17} style={{ color: '#34d399' }} />
              Execution Reliability
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', marginBottom: '0.35rem' }}>
                <span style={{ color: 'var(--text-secondary)' }}>Success vs. Permanent Failure</span>
                <span className="mono-text" style={{ color: '#fff' }}>{successRate}%</span>
              </div>
              <div style={{ height: 8, background: 'var(--bg-tertiary)', borderRadius: 4, overflow: 'hidden', display: 'flex' }}>
                <div
                  style={{
                    width: `${successRate}%`,
                    backgroundColor: '#34d399',
                    transition: 'width 0.3s ease',
                  }}
                />
                <div
                  style={{
                    width: `${100 - successRate}%`,
                    backgroundColor: '#f87171',
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', marginTop: '0.5rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>Avg Execution Duration:</span>
              <span className="mono-text" style={{ color: '#fff', fontWeight: 600 }}>
                {metrics?.execution_duration && metrics.execution_duration.sample_count > 0
                  ? `${formatDurationMetric(metrics.execution_duration.avg_seconds)} (min: ${formatDurationMetric(metrics.execution_duration.min_seconds)}, max: ${formatDurationMetric(metrics.execution_duration.max_seconds)})`
                  : '—'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>Total Retries Recorded:</span>
              <span className="mono-text" style={{ color: '#fbbf24', fontWeight: 600 }}>
                {metrics?.total_retries ?? 0}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>Dispatcher Mode:</span>
              <span className="mono-text" style={{ color: '#94a3b8' }}>Transactional Outbox</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>Active Workers:</span>
              <span className="mono-text" style={{ color: '#60a5fa', fontWeight: 600 }}>
                {metrics?.active_workers_count ?? 0} online
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Recent Jobs Table */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="card-header">
          <div className="card-title">
            <Layers size={17} />
            Recent Jobs
          </div>
          <button className="btn btn-secondary btn-sm" onClick={() => onNavigateExplorer()}>
            <span>View All Jobs</span>
            <ArrowRight size={14} />
          </button>
        </div>
        <JobTable jobs={recentJobs} onSelectJob={onSelectJob} />
      </div>

      {/* Distinct Failure Insights & Analysis (Replaced redundant second JobTable) */}
      <div className="card">
        <div className="card-header">
          <div className="card-title" style={{ color: '#f87171' }}>
            <AlertTriangle size={17} />
            Failure Analysis & Root Cause Breakdown
          </div>
          {failedJobs.length > 0 && (
            <button className="btn btn-secondary btn-sm" onClick={() => onNavigateExplorer('FAILED')}>
              <span>View All Failed Jobs ({failedJobs.length})</span>
              <ArrowRight size={14} />
            </button>
          )}
        </div>

        {failedJobs.length === 0 ? (
          <div className="empty-state" style={{ padding: '1.5rem', textAlign: 'center' }}>
            <CheckCircle size={28} style={{ color: '#34d399', marginBottom: '0.5rem' }} />
            <p style={{ color: '#fff', fontWeight: 500 }}>No terminal failures recorded</p>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              All executed jobs completed cleanly or retried to success.
            </span>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1rem' }}>
              {Object.entries(failureTypeCounts).map(([cause, count]) => (
                <div
                  key={cause}
                  style={{
                    padding: '0.75rem 1rem',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'rgba(239, 68, 68, 0.08)',
                    border: '1px solid rgba(239, 68, 68, 0.25)',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <span style={{ fontSize: '0.85rem', color: '#fca5a5', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginRight: '0.5rem' }}>
                    {cause}
                  </span>
                  <span className="mono-text" style={{ fontSize: '0.85rem', fontWeight: 700, color: '#f87171' }}>
                    {count} {count === 1 ? 'incident' : 'incidents'}
                  </span>
                </div>
              ))}
            </div>

            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Job ID</th>
                    <th>Task Type</th>
                    <th>Attempts</th>
                    <th>Failed Reason</th>
                    <th>Timestamp</th>
                  </tr>
                </thead>
                <tbody>
                  {failedJobs.slice(0, 5).map((fj) => (
                    <tr
                      key={fj.id}
                      onClick={() => onSelectJob(fj)}
                      style={{ cursor: 'pointer' }}
                      title="Click to view job details & AI failure diagnostics"
                    >
                      <td>
                        <span className="mono-text" style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>
                          {fj.id.substring(0, 8)}...
                        </span>
                      </td>
                      <td>
                        <span className="mono-text">{fj.task_type}</span>
                      </td>
                      <td>
                        <span className="mono-text" style={{ color: '#f87171' }}>
                          {fj.attempt_count} / {fj.max_attempts}
                        </span>
                      </td>
                      <td style={{ maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        <span style={{ color: '#fca5a5', fontSize: '0.8rem' }}>
                          {fj.error_message || 'Unregistered or terminal failure'}
                        </span>
                      </td>
                      <td>
                        <span className="mono-text" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          {new Date(fj.updated_at || fj.created_at).toLocaleTimeString()}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
