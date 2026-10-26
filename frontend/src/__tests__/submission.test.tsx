import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { JobSubmit } from '../pages/JobSubmit';
import { api } from '../lib/api';

vi.mock('../lib/api', () => ({
  api: {
    createJob: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    constructor(public status: number, public code: string, message: string, public details?: any) {
      super(message);
    }
  },
}));

describe('JobSubmit Flow', () => {
  it('validates JSON payload format on submit', async () => {
    const onJobCreated = vi.fn();
    render(<JobSubmit onJobCreated={onJobCreated} />);

    const textarea = screen.getByPlaceholderText(/key/i);
    fireEvent.change(textarea, { target: { value: '{ invalid json }' } });

    const submitBtn = screen.getByRole('button', { name: /submit job/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText(/Invalid JSON syntax/i)).toBeInTheDocument();
    });
    expect(api.createJob).not.toHaveBeenCalled();
  });

  it('submits valid job and renders created job feedback', async () => {
    const onJobCreated = vi.fn();
    const mockCreatedJob = {
      id: 'abc-123-job-uuid',
      task_type: 'sleep',
      payload: { seconds: 3 },
      priority: 5,
      status: 'QUEUED',
      attempt_count: 0,
      max_attempts: 3,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    (api.createJob as any).mockResolvedValueOnce(mockCreatedJob);

    render(<JobSubmit onJobCreated={onJobCreated} />);

    const submitBtn = screen.getByRole('button', { name: /submit job/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText(/Job created and dispatched to transactional outbox/i)).toBeInTheDocument();
      expect(screen.getByText('abc-123-job-uuid')).toBeInTheDocument();
    });

    const inspectBtn = screen.getByRole('button', { name: /inspect job details/i });
    fireEvent.click(inspectBtn);
    expect(onJobCreated).toHaveBeenCalledWith(mockCreatedJob);
  });
});
