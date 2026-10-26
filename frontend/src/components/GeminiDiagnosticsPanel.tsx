import React from 'react';
import { FailureDiagnostic } from '../types/api';
import { Sparkles, AlertCircle, RefreshCw, ShieldAlert } from 'lucide-react';

interface GeminiDiagnosticsPanelProps {
  diagnostic: FailureDiagnostic | null;
  isLoading: boolean;
  onAnalyze: () => void;
  isAnalyzing: boolean;
  error?: string | null;
}

export const GeminiDiagnosticsPanel: React.FC<GeminiDiagnosticsPanelProps> = ({
  diagnostic,
  isLoading,
  onAnalyze,
  isAnalyzing,
  error,
}) => {
  const getConfidenceBadge = (confidence: string) => {
    switch (confidence?.toUpperCase()) {
      case 'HIGH':
        return <span className="badge badge-success" style={{ fontSize: '0.75rem' }}>Confidence: HIGH</span>;
      case 'MEDIUM':
        return <span className="badge badge-warning" style={{ fontSize: '0.75rem' }}>Confidence: MEDIUM</span>;
      default:
        return <span className="badge badge-neutral" style={{ fontSize: '0.75rem' }}>Confidence: {confidence || 'LOW'}</span>;
    }
  };

  return (
    <div
      className="card"
      style={{
        marginBottom: '1.5rem',
        border: '1px solid rgba(139, 92, 246, 0.4)',
        background: 'rgba(139, 92, 246, 0.03)',
      }}
    >
      <div className="card-header" style={{ alignItems: 'flex-start' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
            <Sparkles size={18} style={{ color: '#a78bfa' }} />
            <span style={{ fontSize: '1rem', fontWeight: 700, color: '#f3e8ff' }}>
              Gemini AI Failure Diagnostics
            </span>
            <span
              className="badge"
              style={{
                fontSize: '0.7rem',
                background: 'rgba(168, 85, 247, 0.15)',
                color: '#c084fc',
                border: '1px solid rgba(168, 85, 247, 0.3)',
              }}
            >
              Optional AI Advisory
            </span>
          </div>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: 0 }}>
            Root cause analysis synthesized by Google Gemini using sanitized, confidential failure telemetry.
          </p>
        </div>

        <button
          className="btn btn-primary btn-sm"
          onClick={onAnalyze}
          disabled={isAnalyzing}
          style={{
            background: 'linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%)',
            borderColor: '#6366f1',
          }}
        >
          <RefreshCw size={13} className={isAnalyzing ? 'pulse-icon' : ''} />
          <span>{isAnalyzing ? 'Analyzing Failure...' : diagnostic ? 'Re-Analyze with Gemini' : 'Analyze with Gemini AI'}</span>
        </button>
      </div>

      {/* Untrusted AI disclaimer notice */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.5rem 0.75rem',
          background: 'rgba(234, 179, 8, 0.08)',
          border: '1px solid rgba(234, 179, 8, 0.25)',
          borderRadius: 6,
          marginBottom: '1rem',
          fontSize: '0.75rem',
          color: '#fef08a',
        }}
      >
        <ShieldAlert size={14} style={{ flexShrink: 0, color: '#eab308' }} />
        <span>
          <strong>Advisory Notice:</strong> AI suggestions are probabilistic and untrusted. They provide developer guidance but never override queue retry budgets, worker leases, cancellation checkpoints, or deterministic safety rules.
        </span>
      </div>

      {error && !diagnostic && (
        <div className="alert alert-error" style={{ marginBottom: '1rem' }}>
          <AlertCircle size={16} />
          <span style={{ fontSize: '0.85rem' }}>{error}</span>
        </div>
      )}

      {isLoading && !diagnostic ? (
        <div style={{ padding: '1rem', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          Checking stored failure diagnostics...
        </div>
      ) : !diagnostic ? (
        <div
          style={{
            padding: '1.25rem',
            textAlign: 'center',
            background: 'rgba(0, 0, 0, 0.2)',
            borderRadius: 6,
            border: '1px dashed var(--border-subtle)',
          }}
        >
          <p style={{ margin: '0 0 0.5rem 0', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
            No AI root-cause analysis has been computed for this failure attempt yet.
          </p>
          <button
            className="btn btn-secondary btn-sm"
            onClick={onAnalyze}
            disabled={isAnalyzing}
          >
            <Sparkles size={14} style={{ color: '#c084fc' }} />
            <span>Generate Failure Diagnosis</span>
          </button>
        </div>
      ) : (
        <div>
          {/* Top metadata tags */}
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1rem', alignItems: 'center' }}>
            <span
              className="mono-text"
              style={{
                fontSize: '0.8rem',
                fontWeight: 600,
                color: '#c084fc',
                background: 'rgba(168, 85, 247, 0.1)',
                padding: '0.2rem 0.5rem',
                borderRadius: 4,
                border: '1px solid rgba(168, 85, 247, 0.2)',
              }}
            >
              Category: {diagnostic.error_category}
            </span>
            {getConfidenceBadge(diagnostic.confidence)}
            <span
              className={`badge ${diagnostic.retry_may_help ? 'badge-success' : 'badge-neutral'}`}
              style={{ fontSize: '0.75rem' }}
            >
              {diagnostic.retry_may_help ? 'Retry May Help: YES' : 'Retry May Help: NO'}
            </span>
            <span className="mono-text" style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
              Model: {diagnostic.model} · Attempt #{diagnostic.attempt}
            </span>
          </div>

          {/* Likely Cause */}
          <div style={{ marginBottom: '1rem' }}>
            <h4 style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', margin: '0 0 0.35rem 0' }}>
              Likely Root Cause
            </h4>
            <p style={{ margin: 0, fontSize: '0.9rem', color: '#f3e8ff', lineHeight: 1.45 }}>
              {diagnostic.likely_cause}
            </p>
          </div>

          {/* Reasoning Summary */}
          <div style={{ marginBottom: '1rem' }}>
            <h4 style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', margin: '0 0 0.35rem 0' }}>
              Engineering Reasoning Summary
            </h4>
            <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
              {diagnostic.reasoning_summary}
            </p>
          </div>

          {/* Recommended Next Steps */}
          {diagnostic.recommended_next_steps && diagnostic.recommended_next_steps.length > 0 && (
            <div>
              <h4 style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', margin: '0 0 0.5rem 0' }}>
                Recommended Remediation Steps
              </h4>
              <ul style={{ margin: 0, paddingLeft: '1.25rem', color: 'var(--text-primary)', fontSize: '0.85rem', lineHeight: 1.6 }}>
                {diagnostic.recommended_next_steps.map((step, idx) => (
                  <li key={idx} style={{ marginBottom: '0.25rem' }}>
                    {step}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
