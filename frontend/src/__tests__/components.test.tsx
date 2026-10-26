import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBadge } from '../components/StatusBadge';
import { PriorityTag } from '../components/PriorityTag';
import { MetricCard } from '../components/MetricCard';

describe('Frontend UI Components', () => {
  it('renders StatusBadge with normalized status and CSS class', () => {
    const { rerender } = render(<StatusBadge status="RUNNING" />);
    const badge = screen.getByTestId('status-badge-running');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent('RUNNING');
    expect(badge).toHaveClass('running');

    rerender(<StatusBadge status="SUCCEEDED" />);
    const succeededBadge = screen.getByTestId('status-badge-succeeded');
    expect(succeededBadge).toBeInTheDocument();
    expect(succeededBadge).toHaveClass('succeeded');
  });

  it('renders PriorityTag with appropriate priority level and styling', () => {
    const { rerender } = render(<PriorityTag priority={10} />);
    expect(screen.getByText('CRITICAL (10)')).toHaveClass('p-critical');

    rerender(<PriorityTag priority={8} />);
    expect(screen.getByText('HIGH (8)')).toHaveClass('p-high');

    rerender(<PriorityTag priority={5} />);
    expect(screen.getByText('NORMAL (5)')).toHaveClass('p-normal');

    rerender(<PriorityTag priority={1} />);
    expect(screen.getByText('LOW (1)')).toHaveClass('p-low');
  });

  it('renders MetricCard with label, value, and subtitle', () => {
    render(
      <MetricCard
        label="Pending Jobs"
        value={42}
        subtitle="Queue depth"
      />
    );
    expect(screen.getByText('Pending Jobs')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('Queue depth')).toBeInTheDocument();
  });
});
