import React from 'react';
import { Job } from '../types/api';
import { StatusBadge } from './StatusBadge';
import { PriorityTag } from './PriorityTag';

interface JobTableProps {
  jobs: Job[];
  onSelectJob?: (job: Job) => void;
  isLoading?: boolean;
}

export const JobTable: React.FC<JobTableProps> = ({ jobs, onSelectJob, isLoading }) => {
  if (isLoading) {
    return (
      <div className="loading-container">
        <div className="status-badge-dot" style={{ backgroundColor: 'var(--accent-blue)', width: 10, height: 10 }} />
        Loading jobs...
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">No jobs found</div>
        <p>There are no jobs matching the current query or filter criteria.</p>
      </div>
    );
  }

  const formatDate = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    } catch {
      return iso;
    }
  };

  return (
    <div className="table-container">
      <table className="data-table">
        <thead>
          <tr>
            <th>Job ID</th>
            <th>Task Type</th>
            <th>Status</th>
            <th>Priority</th>
            <th>Attempts</th>
            <th>Created At</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr
              key={job.id}
              className={onSelectJob ? 'clickable' : ''}
              onClick={() => onSelectJob && onSelectJob(job)}
              tabIndex={onSelectJob ? 0 : undefined}
              onKeyDown={(e) => {
                if (onSelectJob && (e.key === 'Enter' || e.key === ' ')) {
                  e.preventDefault();
                  onSelectJob(job);
                }
              }}
            >
              <td>
                <span
                  className="mono-text"
                  title={job.id}
                  data-testid={`job-id-${job.id}`}
                  style={{ color: 'var(--accent-blue)', fontWeight: 600 }}
                >
                  {job.id.substring(0, 8)}...
                </span>
              </td>
              <td>
                <span className="mono-text" style={{ color: '#fff' }}>
                  {job.task_type}
                </span>
              </td>
              <td>
                <StatusBadge status={job.status} />
              </td>
              <td>
                <PriorityTag priority={job.priority} />
              </td>
              <td>
                <span className="mono-text">
                  {job.attempt_count} / {job.max_attempts}
                </span>
              </td>
              <td>
                <span className="mono-text" style={{ fontSize: '0.75rem' }}>
                  {formatDate(job.created_at)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
