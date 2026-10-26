import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { GeminiDiagnosticsPanel } from '../components/GeminiDiagnosticsPanel';
import { FailureDiagnostic } from '../types/api';

describe('GeminiDiagnosticsPanel', () => {
  const sampleDiagnostic: FailureDiagnostic = {
    id: 'diag-1',
    job_id: 'job-123',
    attempt: 2,
    model: 'gemini-2.5-flash',
    error_category: 'NETWORK_TIMEOUT',
    likely_cause: 'Downstream HTTP gateway hung during upstream query.',
    confidence: 'HIGH',
    recommended_next_steps: [
      'Increase client timeout beyond 30s',
      'Verify target gateway DNS resolution',
    ],
    retry_may_help: true,
    reasoning_summary: 'Gateway timeout indicates a transient downstream latency spike.',
    created_at: new Date().toISOString(),
  };

  it('renders untrusted advisory notice clearly', () => {
    render(
      <GeminiDiagnosticsPanel
        diagnostic={null}
        isLoading={false}
        onAnalyze={vi.fn()}
        isAnalyzing={false}
      />
    );

    expect(screen.getByText(/Advisory Notice:/i)).toBeInTheDocument();
    expect(screen.getByText(/AI suggestions are probabilistic and untrusted/i)).toBeInTheDocument();
    expect(screen.getByText(/No AI root-cause analysis has been computed/i)).toBeInTheDocument();
  });

  it('renders diagnostic fields and recommendations when available', () => {
    const onAnalyzeMock = vi.fn();
    render(
      <GeminiDiagnosticsPanel
        diagnostic={sampleDiagnostic}
        isLoading={false}
        onAnalyze={onAnalyzeMock}
        isAnalyzing={false}
      />
    );

    expect(screen.getByText(/Category: NETWORK_TIMEOUT/i)).toBeInTheDocument();
    expect(screen.getByText(/Confidence: HIGH/i)).toBeInTheDocument();
    expect(screen.getByText(/Retry May Help: YES/i)).toBeInTheDocument();
    expect(screen.getByText(sampleDiagnostic.likely_cause)).toBeInTheDocument();
    expect(screen.getByText(sampleDiagnostic.reasoning_summary)).toBeInTheDocument();
    expect(screen.getByText(/Increase client timeout beyond 30s/i)).toBeInTheDocument();

    const reanalyzeBtn = screen.getByRole('button', { name: /Re-Analyze with Gemini/i });
    fireEvent.click(reanalyzeBtn);
    expect(onAnalyzeMock).toHaveBeenCalledTimes(1);
  });

  it('displays loading state while analyzing', () => {
    render(
      <GeminiDiagnosticsPanel
        diagnostic={null}
        isLoading={false}
        onAnalyze={vi.fn()}
        isAnalyzing={true}
      />
    );

    expect(screen.getByText(/Analyzing Failure.../i)).toBeInTheDocument();
  });
});
