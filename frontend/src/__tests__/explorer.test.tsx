import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { JobExplorer } from '../pages/JobExplorer';
import { api } from '../lib/api';
import { Job } from '../types/api';

vi.mock('../lib/api', () => ({
  api: {
    listJobs: vi.fn(),
    getJob: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    constructor(public status: number, public code: string, message: string) {
      super(message);
    }
  },
}));

describe('JobExplorer Page', () => {
  const mockJobs: Job[] = [
    {
      id: 'job-1111-aaaa',
      task_type: 'generate_report',
      payload: { target: 'monthly' },
      priority: 8,
      status: 'QUEUED',
      attempt_count: 0,
      max_attempts: 3,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
    {
      id: 'job-2222-bbbb',
      task_type: 'http_webhook',
      payload: { url: 'https://example.com' },
      priority: 5,
      status: 'SUCCEEDED',
      attempt_count: 1,
      max_attempts: 3,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders filters, table header, and paginated jobs list', async () => {
    vi.mocked(api.listJobs).mockResolvedValueOnce({
      items: mockJobs,
      total: 2,
      page: 1,
      page_size: 15,
      total_pages: 1,
    });

    const onSelectJobMock = vi.fn();
    render(<JobExplorer onSelectJob={onSelectJobMock} />);

    expect(screen.getByPlaceholderText(/Paste UUID.../i)).toBeInTheDocument();
    expect(screen.getByText(/All Statuses/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('generate_report')).toBeInTheDocument();
      expect(screen.getByText('http_webhook')).toBeInTheDocument();
    });

    expect(screen.getByTestId('job-id-job-1111-aaaa')).toBeInTheDocument();
    expect(screen.getByTestId('job-id-job-2222-bbbb')).toBeInTheDocument();
  });

  it('allows filtering by status and updates the query parameters', async () => {
    vi.mocked(api.listJobs).mockResolvedValue({
      items: [mockJobs[0]],
      total: 1,
      page: 1,
      page_size: 15,
      total_pages: 1,
    });

    render(<JobExplorer onSelectJob={vi.fn()} />);

    await waitFor(() => {
      expect(api.listJobs).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, page_size: 15 })
      );
    });

    const statusSelect = screen.getByDisplayValue(/All Statuses/i);
    fireEvent.change(statusSelect, { target: { value: 'QUEUED' } });

    await waitFor(() => {
      expect(api.listJobs).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'QUEUED' })
      );
    });
  });

  it('allows clicking a row to select a job', async () => {
    vi.mocked(api.listJobs).mockResolvedValueOnce({
      items: mockJobs,
      total: 2,
      page: 1,
      page_size: 15,
      total_pages: 1,
    });

    const onSelectJobMock = vi.fn();
    render(<JobExplorer onSelectJob={onSelectJobMock} />);

    await waitFor(() => {
      expect(screen.getByText('generate_report')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('generate_report'));
    expect(onSelectJobMock).toHaveBeenCalledWith(mockJobs[0]);
  });
});
