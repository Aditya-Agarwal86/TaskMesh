import React from 'react';
import { api, ApiError } from '../lib/api';
import { Job } from '../types/api';
import { CheckCircle2, AlertTriangle, ArrowRight, Copy, Check } from 'lucide-react';

interface JobSubmitProps {
  onJobCreated: (job: Job) => void;
}

const PRESET_TASKS = [
  {
    type: 'sleep',
    description: 'Simulates work by pausing execution with cooperative cancellation checkpoints.',
    defaultPayload: { duration_seconds: 1.5, checkpoint_interval: 0.5 },
    schemaHint: 'duration_seconds (0-300), checkpoint_interval (0.1-10)',
  },
  {
    type: 'generate_report',
    description: 'Generates structured analytics report with deterministic data hashing.',
    defaultPayload: { report_name: 'throughput_report', format: 'summary', dataset: [{ count: 10 }, { count: 25 }] },
    schemaHint: 'report_name (string), dataset (array of objects), format ("summary"|"detailed"|"json")',
  },
  {
    type: 'email_notification',
    description: 'Simulates queued transactional email notification delivery.',
    defaultPayload: { to: 'engineer@taskmesh.internal', template: 'deployment_alert' },
    schemaHint: 'to (email string), template (string)',
  },
  {
    type: 'invoice_generation',
    description: 'Renders and stores a deterministic PDF customer invoice.',
    defaultPayload: { order_id: 1042 },
    schemaHint: 'order_id (positive integer)',
  },
  {
    type: 'pdf_render',
    description: 'Renders a multi-page document into formatted artifact.',
    defaultPayload: { document_id: 'doc-report-2026' },
    schemaHint: 'document_id (string)',
  },
  {
    type: 'data_sync',
    description: 'Synchronizes records in batches to simulate ETL ingestion.',
    defaultPayload: { records_count: 50 },
    schemaHint: 'records_count (integer 1-100000)',
  },
  {
    type: 'fail_once',
    description: 'Fails on first execution and recovers on automatic retry using persisted state.',
    defaultPayload: { key: 'sample-retry-demo' },
    schemaHint: 'key (optional string)',
  },
  {
    type: 'always_fail',
    description: 'Permanently fails with non-retryable error to test DLQ & failure diagnostics.',
    defaultPayload: { reason: 'Permanent database constraint violation simulation' },
    schemaHint: 'reason (optional string)',
  },
];

export const JobSubmit: React.FC<JobSubmitProps> = ({ onJobCreated }) => {
  const [taskType, setTaskType] = React.useState('sleep');
  const [priority, setPriority] = React.useState<number>(5);
  const [maxAttempts, setMaxAttempts] = React.useState<number>(3);
  const [idempotencyKey, setIdempotencyKey] = React.useState<string>('');
  const [payloadText, setPayloadText] = React.useState<string>(
    JSON.stringify({ duration_seconds: 1.5, checkpoint_interval: 0.5 }, null, 2)
  );

  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [validationError, setValidationError] = React.useState<string | null>(null);
  const [apiError, setApiError] = React.useState<string | null>(null);
  const [isExistingJob, setIsExistingJob] = React.useState(false);
  const [createdJob, setCreatedJob] = React.useState<Job | null>(null);
  const [copied, setCopied] = React.useState(false);

  const handleSelectPreset = (preset: typeof PRESET_TASKS[0]) => {
    setTaskType(preset.type);
    setPayloadText(JSON.stringify(preset.defaultPayload, null, 2));
    setValidationError(null);
  };

  const handleCopyId = () => {
    if (createdJob) {
      navigator.clipboard.writeText(createdJob.id);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);
    setApiError(null);
    setCreatedJob(null);
    setIsExistingJob(false);

    if (!taskType) {
      setValidationError('Please select a valid registered task type.');
      return;
    }

    // Validate JSON payload
    let parsedPayload: Record<string, any> = {};
    if (payloadText.trim()) {
      try {
        parsedPayload = JSON.parse(payloadText);
        if (typeof parsedPayload !== 'object' || Array.isArray(parsedPayload)) {
          setValidationError('Payload must be a valid JSON object (e.g. {"key": "val"}).');
          return;
        }
      } catch (err: any) {
        setValidationError(`Invalid JSON syntax: ${err.message}`);
        return;
      }
    }

    setIsSubmitting(true);
    try {
      const job = await api.createJob({
        task_type: taskType,
        payload: parsedPayload,
        priority,
        max_attempts: maxAttempts,
        idempotency_key: idempotencyKey.trim() || undefined,
      });

      // If job already had attempts or updated earlier, flag as existing job
      if (job.attempt_count > 0 || (job.created_at && new Date(job.created_at).getTime() < Date.now() - 5000)) {
        setIsExistingJob(true);
      }
      setCreatedJob(job);
    } catch (err: any) {
      if (err instanceof ApiError) {
        let details = '';
        if (err.details && Array.isArray(err.details)) {
          details = err.details.map((d: any) => `${d.loc?.join('.')}: ${d.msg}`).join(', ');
        }
        setApiError(`${err.code}: ${err.message} ${details ? `(${details})` : ''}`);
      } else {
        setApiError(err.message || 'An unexpected error occurred during submission.');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const selectedPreset = PRESET_TASKS.find((p) => p.type === taskType);

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      {createdJob && (
        <div className="alert alert-success" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', fontWeight: 600 }}>
            <CheckCircle2 size={18} />
            <span>
              {isExistingJob
                ? 'Idempotent Match: Returned existing recorded job matching submission parameters.'
                : 'Job created and dispatched to transactional outbox.'}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '0.85rem' }}>Job ID:</span>
            <span className="mono-text" style={{ background: 'rgba(0,0,0,0.2)', padding: '0.2rem 0.5rem', borderRadius: 4 }}>
              {createdJob.id}
            </span>
            <button className="btn btn-secondary btn-sm" onClick={handleCopyId} title="Copy UUID">
              {copied ? <Check size={13} /> : <Copy size={13} />}
              <span>{copied ? 'Copied' : 'Copy'}</span>
            </button>
            <button className="btn btn-primary btn-sm" onClick={() => onJobCreated(createdJob)}>
              <span>Inspect Job Details</span>
              <ArrowRight size={14} />
            </button>
          </div>
        </div>
      )}

      {apiError && (
        <div className="alert alert-error">
          <AlertTriangle size={18} />
          <div>{apiError}</div>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <div className="card-title">Submit Validated Distributed Task</div>
        </div>

        <form onSubmit={handleSubmit}>
          {/* Preset Buttons */}
          <div className="form-group">
            <label className="form-label">Registered Task Type</label>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '0.5rem', marginBottom: '0.75rem' }}>
              {PRESET_TASKS.map((preset) => (
                <button
                  type="button"
                  key={preset.type}
                  className={`btn btn-sm ${taskType === preset.type ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => handleSelectPreset(preset)}
                  style={{ textAlign: 'left', justifyContent: 'flex-start' }}
                >
                  <span className="mono-text" style={{ fontWeight: 600 }}>{preset.type}</span>
                </button>
              ))}
            </div>
            <div className="form-helper">
              {selectedPreset?.description}
            </div>
            {selectedPreset?.schemaHint && (
              <div style={{ fontSize: '0.75rem', color: 'var(--accent-blue)', marginTop: '0.25rem', fontFamily: 'var(--font-mono)' }}>
                Schema: {selectedPreset.schemaHint}
              </div>
            )}
          </div>

          {/* Priority & Max Attempts */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div className="form-group">
              <label className="form-label">Priority</label>
              <select
                className="form-select"
                value={priority}
                onChange={(e) => setPriority(parseInt(e.target.value, 10))}
              >
                <option value={1}>LOW (1)</option>
                <option value={5}>NORMAL (5 - Default)</option>
                <option value={8}>HIGH (8)</option>
                <option value={10}>CRITICAL (10 - Dispatched First)</option>
              </select>
              <div className="form-helper">Higher priority jobs are popped first from Redis ZSET.</div>
            </div>

            <div className="form-group">
              <label className="form-label">Max Attempts</label>
              <input
                type="number"
                min={1}
                max={10}
                className="form-input"
                value={maxAttempts}
                onChange={(e) => setMaxAttempts(parseInt(e.target.value, 10))}
              />
              <div className="form-helper">Configured attempts before permanent failure transition.</div>
            </div>
          </div>

          {/* Idempotency Key */}
          <div className="form-group">
            <label className="form-label">Idempotency Key (Optional)</label>
            <input
              type="text"
              className="form-input"
              placeholder="e.g. order-payment-9481"
              value={idempotencyKey}
              onChange={(e) => setIdempotencyKey(e.target.value)}
            />
            <div className="form-helper">
              Unique submission scope: Repeated submissions with the same key and equivalent request return the existing job. External side effects depend on handler idempotency.
            </div>
          </div>

          {/* JSON Payload */}
          <div className="form-group">
            <label className="form-label">JSON Payload</label>
            <textarea
              className="form-textarea"
              value={payloadText}
              onChange={(e) => setPayloadText(e.target.value)}
              placeholder='{\n  "key": "value"\n}'
              rows={6}
            />
            {validationError ? (
              <div className="form-error">{validationError}</div>
            ) : (
              <div className="form-helper">Structured JSON payload passed to the registered handler.</div>
            )}
          </div>

          {/* Submit Button */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '1.5rem' }}>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={isSubmitting}
              style={{ minWidth: 160 }}
            >
              {isSubmitting ? 'Submitting...' : 'Submit Job'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
