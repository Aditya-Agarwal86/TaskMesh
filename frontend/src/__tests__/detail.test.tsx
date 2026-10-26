import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { JobDetail } from '../pages/JobDetail';
import { api } from '../lib/api';
import { Job } from '../types/api';

vi.mock('../lib/api', () => ({
  api: {
    getJob: vi.fn(),
    getJobEvents: vi.fn(),
    cancelJob: vi.fn(),
    retryJob: vi.fn(),
    getJobDiagnostics: vi.fn(),
    analyzeJobFailure: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    constructor(public status: number, public code: string, message: string) {
      super(message);
    }
  },
}));

describe('JobDetail Page Controls and States', () => {
  const baseJob: Job = {
    id: 'test-job-uuid-1234',
    task_type: 'generate_report',
    payload: { format: 'pdf' },
    result: null,
    priority: 8,
    status: 'RUNNING',
    attempt_count: 1,
    max_attempts: 3,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders loading state initially', () => {
    vi.mocked(api.getJob).mockImplementation(() => new Promise(() => {}));
    vi.mocked(api.getJobEvents).mockImplementation(() => new Promise(() => {}));

    render(<JobDetail jobId="test-job-uuid-1234" onBack={vi.fn()} />);
    expect(screen.getByText(/Loading job metadata and state timeline/i)).toBeInTheDocument();
  });

  it('renders cancel button for running job and handles click', async () => {
    vi.mocked(api.getJob).mockResolvedValueOnce(baseJob);
    vi.mocked(api.getJobEvents).mockResolvedValueOnce([]);
    vi.mocked(api.cancelJob).mockResolvedValueOnce({ ...baseJob, status: 'CANCELLED' });

    // Mock confirm dialog
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<JobDetail jobId="test-job-uuid-1234" onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('generate_report')).toBeInTheDocument();
    });

    const cancelBtn = screen.getByRole('button', { name: /Cancel Job/i });
    expect(cancelBtn).toBeInTheDocument();

    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(api.cancelJob).toHaveBeenCalledWith('test-job-uuid-1234', expect.any(String));
    });
  });

  it('renders retry button for failed job and handles click', async () => {
    const failedJob: Job = {
      ...baseJob,
      status: 'FAILED',
      error_message: 'Downstream HTTP 500 error',
    };

    vi.mocked(api.getJob).mockResolvedValueOnce(failedJob);
    vi.mocked(api.getJobEvents).mockResolvedValueOnce([]);
    vi.mocked(api.getJobDiagnostics).mockResolvedValueOnce(null);
    vi.mocked(api.retryJob).mockResolvedValueOnce({ ...failedJob, status: 'QUEUED' });

    render(<JobDetail jobId="test-job-uuid-1234" onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Downstream HTTP 500 error')).toBeInTheDocument();
    });

    const retryBtn = screen.getByRole('button', { name: /Retry Job/i });
    expect(retryBtn).toBeInTheDocument();

    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(api.retryJob).toHaveBeenCalledWith('test-job-uuid-1234');
    });
  });

  it('renders error state when job fetch fails with 404', async () => {
    vi.mocked(api.getJob).mockRejectedValueOnce(new Error('Job not found'));
    vi.mocked(api.getJobEvents).mockResolvedValueOnce([]);

    render(<JobDetail jobId="missing-uuid" onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/Job not found/i)).toBeInTheDocument();
    });
  });
});
