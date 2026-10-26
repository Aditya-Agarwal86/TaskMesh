import React from 'react';
import { api } from '../lib/api';
import { SystemMetrics, Job } from '../types/api';
import { MetricCard } from '../components/MetricCard';
import { JobTable } from '../components/JobTable';
import {
  Cpu,
  Layers,
  Activity,
  Heart,
  Server,
  Zap,
  AlertTriangle,
} from 'lucide-react';

interface WorkersQueueProps {
  onSelectJob: (job: Job) => void;
  refreshTrigger?: number;
}

export const WorkersQueue: React.FC<WorkersQueueProps> = ({ onSelectJob, refreshTrigger }) => {
  const [metrics, setMetrics] = React.useState<SystemMetrics | null>(null);
  const [activeJobs, setActiveJobs] = React.useState<Job[]>([]);
  const [readiness, setReadiness] = React.useState<{ database: string; redis: string } | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const fetchWorkersAndQueue = React.useCallback(async (silent = false) => {
    try {
      if (!silent) {
        setError(null);
      }
      const [m, r, runningRes] = await Promise.all([
        api.getMetrics(),
        api.getReadiness().catch(() => null),
        api.listJobs({ status: 'RUNNING', page_size: 20 }),
      ]);
      setMetrics(m);
      setReadiness(r ? { database: r.database, redis: r.redis } : null);
      setActiveJobs(runningRes.items || []);
    } catch (err: any) {
      if (!silent) {
        setError(err.message || 'Error fetching worker and queue state.');
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    fetchWorkersAndQueue();
    const interval = setInterval(() => fetchWorkersAndQueue(true), 4000);
    return () => clearInterval(interval);
  }, [fetchWorkersAndQueue]);

  React.useEffect(() => {
    if (refreshTrigger !== undefined && refreshTrigger > 0) {
      fetchWorkersAndQueue(true);
    }
  }, [refreshTrigger, fetchWorkersAndQueue]);

  if (isLoading && !metrics) {
    return (
      <div className="loading-container">
        <Activity size={20} className="pulse-icon" />
        <span>Gathering cluster worker metrics...</span>
      </div>
    );
  }

  // Extract unique active worker IDs from running jobs
  const workerMap: Record<string, { jobId: string; taskType: string; startedAt: string; leaseExpiresAt?: string | null }> = {};
  for (const j of activeJobs) {
    if (j.worker_id) {
      workerMap[j.worker_id] = {
        jobId: j.id,
        taskType: j.task_type,
        startedAt: j.started_at || j.updated_at,
        leaseExpiresAt: j.lease_expires_at,
      };
    }
  }

  const queueDepth = metrics?.queue_depth || { pending: 0, processing: 0, dlq: 0 };
  const workers = metrics?.workers || [];
  const activeWorkerCount = metrics?.active_workers_count ?? workers.length;

  return (
    <div>
      {error && (
        <div className="alert alert-error">
          <AlertTriangle size={18} />
          <div>{error}</div>
        </div>
      )}

      {/* Warning if jobs queued or pending with no healthy workers online */}
      {queueDepth.pending > 0 && activeWorkerCount === 0 && (
        <div className="alert alert-warning" style={{ marginBottom: '1.25rem', backgroundColor: 'rgba(245, 158, 11, 0.1)', borderColor: 'rgba(245, 158, 11, 0.3)' }}>
          <AlertTriangle size={18} style={{ color: '#fbbf24' }} />
          <div>
            <strong style={{ color: '#fbbf24' }}>No Online Workers Detected:</strong> {queueDepth.pending} job(s) are queued in Redis priority queue, but no active worker heartbeats were found. Ensure worker processes are running and communicating with Redis.
          </div>
        </div>
      )}

      {/* Cluster Infrastructure Health Banner */}
      <div className="card" style={{ marginBottom: '1.5rem', background: 'var(--bg-secondary)' }}>
        <div className="card-header" style={{ marginBottom: '0.75rem' }}>
          <div className="card-title">
            <Server size={17} style={{ color: 'var(--accent-blue)' }} />
            Infrastructure Status (Live Probes)
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <div className="status-badge-dot" style={{ backgroundColor: readiness?.database === 'ok' ? '#34d399' : '#f87171' }} />
            <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>PostgreSQL Outbox & State:</span>
            <span className="mono-text" style={{ color: '#fff', fontWeight: 600 }}>{readiness?.database || 'OK'}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <div className="status-badge-dot" style={{ backgroundColor: readiness?.redis === 'ok' ? '#34d399' : '#f87171' }} />
            <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Redis Priority Engine:</span>
            <span className="mono-text" style={{ color: '#fff', fontWeight: 600 }}>{readiness?.redis || 'OK'}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <div className="status-badge-dot" style={{ backgroundColor: '#34d399' }} />
            <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Dispatcher Heartbeat:</span>
            <span className="mono-text" style={{ color: '#fff', fontWeight: 600 }}>1.0s loop</span>
          </div>
        </div>
      </div>

      {/* Queue Depth & Telemetry Section */}
      <div className="metrics-grid">
        <MetricCard
          label="Pending Queue Depth"
          value={queueDepth.pending}
          subtitle="Waiting in Redis priority ZSET"
          icon={<Layers size={18} />}
        />
        <MetricCard
          label="Processing Leases"
          value={queueDepth.processing}
          subtitle="Active leases in Redis"
          icon={<Cpu size={18} />}
        />
        <MetricCard
          label="Dead Letter Queue"
          value={queueDepth.dlq}
          subtitle="Exhausted retry attempts"
          icon={<AlertTriangle size={18} />}
        />
        <MetricCard
          label="Active Workers"
          value={activeWorkerCount}
          subtitle={`${workers.filter((w) => w.status === 'BUSY').length} busy, ${workers.filter((w) => w.status === 'IDLE').length} idle`}
          icon={<Heart size={18} />}
        />
        <MetricCard
          label="Completed Jobs"
          value={metrics?.completed_jobs ?? 0}
          subtitle="Successfully executed"
          icon={<Zap size={18} />}
        />
        <MetricCard
          label="Avg Duration"
          value={
            metrics?.execution_duration && metrics.execution_duration.sample_count > 0
              ? metrics.execution_duration.avg_seconds < 1.0
                ? `${Math.round(metrics.execution_duration.avg_seconds * 1000)}ms`
                : `${metrics.execution_duration.avg_seconds}s`
              : '0s'
          }
          subtitle={`${metrics?.execution_duration?.sample_count ?? 0} samples`}
          icon={<Activity size={18} />}
        />
      </div>

      {/* Priority Ordering Policy Reference */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="card-header">
          <div className="card-title">
            <Zap size={16} />
            Priority Scheduling & Atomic Leases
          </div>
        </div>
        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
          RedisPriorityQueue uses atomic Lua scripts to safely transition jobs from priority-scored sorted sets (ZSET)
          to active leases without race conditions. Priority levels:
        </p>
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          <div className="priority-tag p-critical">CRITICAL (Score: 10)</div>
          <div className="priority-tag p-high">HIGH (Score: 8)</div>
          <div className="priority-tag p-normal">NORMAL (Score: 5)</div>
          <div className="priority-tag p-low">LOW (Score: 1)</div>
        </div>
      </div>

      {/* Real Worker Presence & Registry Table */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="card-header">
          <div className="card-title">
            <Cpu size={17} />
            Registered Workers ({workers.length})
          </div>
        </div>

        {workers.length === 0 ? (
          <div className="empty-state" style={{ padding: '2rem', textAlign: 'center' }}>
            <Server size={32} style={{ color: 'var(--text-muted)', marginBottom: '0.75rem' }} />
            <p style={{ color: '#fff', fontWeight: 500 }}>No active workers currently registered</p>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
              Workers report heartbeats every 4s via Redis presence keys with a 15s expiration policy.
            </span>
          </div>
        ) : (
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Worker ID</th>
                  <th>Status</th>
                  <th>Current Job ID</th>
                  <th>Last Heartbeat</th>
                  <th>Lease Expiration</th>
                </tr>
              </thead>
              <tbody>
                {workers.map((w) => (
                  <tr key={w.worker_id}>
                    <td>
                      <span className="mono-text" style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>
                        {w.worker_id}
                      </span>
                    </td>
                    <td>
                      <span className={`status-badge ${w.status === 'BUSY' ? 'running' : 'succeeded'}`}>
                        <span className="status-badge-dot" />
                        {w.status}
                      </span>
                    </td>
                    <td>
                      {w.current_job_id ? (
                        <span
                          className="mono-text"
                          style={{ fontSize: '0.8rem', color: 'var(--accent-blue)', cursor: 'pointer' }}
                          onClick={() => {
                            const found = activeJobs.find((j) => j.id === w.current_job_id);
                            if (found) onSelectJob(found);
                          }}
                          title="Click to view job details"
                        >
                          {w.current_job_id.substring(0, 8)}...
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>Awaiting work</span>
                      )}
                    </td>
                    <td>
                      <span className="mono-text" style={{ fontSize: '0.75rem' }}>
                        {w.last_heartbeat ? new Date(w.last_heartbeat).toLocaleTimeString() : '—'}
                      </span>
                    </td>
                    <td>
                      <span className="mono-text" style={{ fontSize: '0.75rem', color: w.lease_expires_at ? '#fbbf24' : 'var(--text-muted)' }}>
                        {w.lease_expires_at ? new Date(w.lease_expires_at).toLocaleTimeString() : 'None (Idle)'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Running Jobs Table */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">
            <Activity size={17} />
            Live In-Flight Jobs ({activeJobs.length})
          </div>
        </div>
        <JobTable jobs={activeJobs} onSelectJob={onSelectJob} />
      </div>
    </div>
  );
};
