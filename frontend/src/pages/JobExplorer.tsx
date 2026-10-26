import React from 'react';
import { api, ApiError } from '../lib/api';
import { Job } from '../types/api';
import { JobTable } from '../components/JobTable';
import { Search, ChevronLeft, ChevronRight, X, AlertTriangle, Filter } from 'lucide-react';

interface JobExplorerProps {
  onSelectJob: (job: Job) => void;
  initialStatus?: string;
  onClearInitialStatus?: () => void;
  refreshTrigger?: number;
}

export const JobExplorer: React.FC<JobExplorerProps> = ({
  onSelectJob,
  initialStatus,
  onClearInitialStatus,
  refreshTrigger,
}) => {
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [searchId, setSearchId] = React.useState('');
  const [statusFilter, setStatusFilter] = React.useState<string>(initialStatus || '');
  const [priorityFilter, setPriorityFilter] = React.useState<string>('');
  const [taskTypeFilter, setTaskTypeFilter] = React.useState<string>('');
  const [page, setPage] = React.useState(1);
  const [pageSize] = React.useState(15);
  const [totalPages, setTotalPages] = React.useState(1);
  const [totalCount, setTotalCount] = React.useState(0);
  const [isInitialLoading, setIsInitialLoading] = React.useState(true);
  const [isLoading, setIsLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const fetchJobs = React.useCallback(async (silent = false) => {
    if (!silent) {
      setIsLoading(true);
      setError(null);
    }

    // If an exact search by ID is entered
    if (searchId.trim()) {
      try {
        const job = await api.getJob(searchId.trim());
        setJobs([job]);
        setTotalCount(1);
        setTotalPages(1);
      } catch (err: any) {
        if (err instanceof ApiError && err.status === 404) {
          setJobs([]);
          setTotalCount(0);
          setTotalPages(1);
        } else if (!silent) {
          setError(err.message || 'Error searching for job');
        }
      } finally {
        setIsLoading(false);
        setIsInitialLoading(false);
      }
      return;
    }

    try {
      const res = await api.listJobs({
        status: statusFilter || undefined,
        priority: priorityFilter ? parseInt(priorityFilter, 10) : undefined,
        task_type: taskTypeFilter || undefined,
        page,
        page_size: pageSize,
      });

      setJobs(res.items || []);
      setTotalPages(res.total_pages || 1);
      setTotalCount(res.total || 0);
    } catch (err: any) {
      if (!silent) {
        setError(err.message || 'Failed to fetch jobs.');
      }
    } finally {
      setIsLoading(false);
      setIsInitialLoading(false);
    }
  }, [searchId, statusFilter, priorityFilter, taskTypeFilter, page, pageSize]);

  React.useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  React.useEffect(() => {
    if (refreshTrigger !== undefined && refreshTrigger > 0) {
      fetchJobs(true);
    }
  }, [refreshTrigger, fetchJobs]);

  const handleResetFilters = () => {
    setSearchId('');
    setStatusFilter('');
    setPriorityFilter('');
    setTaskTypeFilter('');
    setPage(1);
    if (onClearInitialStatus) {
      onClearInitialStatus();
    }
  };

  const hasActiveFilters = Boolean(searchId || statusFilter || priorityFilter || taskTypeFilter);

  return (
    <div>
      {error && (
        <div className="alert alert-error">
          <AlertTriangle size={18} />
          <div>{error}</div>
        </div>
      )}

      {/* Filter Toolbar */}
      <div className="card" style={{ marginBottom: '1.25rem', padding: '1rem' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.75rem', alignItems: 'end' }}>
          <div>
            <label className="form-label">Search Job ID</label>
            <div style={{ position: 'relative' }}>
              <input
                type="text"
                className="form-input"
                placeholder="Paste UUID..."
                value={searchId}
                onChange={(e) => {
                  setSearchId(e.target.value);
                  setPage(1);
                }}
                style={{ paddingLeft: '2rem' }}
              />
              <Search
                size={14}
                style={{ position: 'absolute', left: '0.65rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }}
              />
            </div>
          </div>

          <div>
            <label className="form-label">Status</label>
            <select
              className="form-select"
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="">All Statuses</option>
              <option value="QUEUED">QUEUED</option>
              <option value="RUNNING">RUNNING</option>
              <option value="RETRYING">RETRYING</option>
              <option value="SUCCEEDED">SUCCEEDED</option>
              <option value="FAILED">FAILED</option>
              <option value="CANCELLED">CANCELLED</option>
            </select>
          </div>

          <div>
            <label className="form-label">Priority</label>
            <select
              className="form-select"
              value={priorityFilter}
              onChange={(e) => {
                setPriorityFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="">All Priorities</option>
              <option value="1">LOW (1)</option>
              <option value="5">NORMAL (5)</option>
              <option value="8">HIGH (8)</option>
              <option value="10">CRITICAL (10)</option>
            </select>
          </div>

          <div>
            <label className="form-label">Task Type</label>
            <input
              type="text"
              className="form-input"
              placeholder="e.g. sleep, generate_report"
              value={taskTypeFilter}
              onChange={(e) => {
                setTaskTypeFilter(e.target.value);
                setPage(1);
              }}
            />
          </div>

          {hasActiveFilters && (
            <div>
              <button className="btn btn-secondary" onClick={handleResetFilters} style={{ width: '100%' }}>
                <X size={14} />
                <span>Clear Filters</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Main Table */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">
            <Filter size={16} />
            <span>Jobs ({totalCount})</span>
          </div>

          {/* Pagination Controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginRight: '0.5rem' }}>
              Page {page} of {totalPages}
            </span>
            <button
              className="btn btn-secondary btn-sm"
              disabled={page <= 1 || isLoading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <ChevronLeft size={15} />
            </button>
            <button
              className="btn btn-secondary btn-sm"
              disabled={page >= totalPages || isLoading}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>

        <JobTable jobs={jobs} onSelectJob={onSelectJob} isLoading={isInitialLoading && jobs.length === 0} />
      </div>
    </div>
  );
};
