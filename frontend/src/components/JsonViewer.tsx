import React from 'react';
import { ChevronDown, ChevronRight, Copy, Check } from 'lucide-react';

interface JsonViewerProps {
  data: any;
  title?: string;
  defaultCollapsed?: boolean;
}

export const JsonViewer: React.FC<JsonViewerProps> = ({ data, title, defaultCollapsed = false }) => {
  const [collapsed, setCollapsed] = React.useState(defaultCollapsed);
  const [copied, setCopied] = React.useState(false);

  const jsonString = React.useMemo(() => {
    if (data === undefined || data === null) return 'null';
    try {
      return JSON.stringify(data, null, 2);
    } catch {
      return String(data);
    }
  }, [data]);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(jsonString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          cursor: 'pointer',
          padding: '0.35rem 0.5rem',
          background: 'rgba(255, 255, 255, 0.03)',
          borderRadius: 4,
          marginBottom: collapsed ? 0 : '0.4rem',
        }}
        onClick={() => setCollapsed(!collapsed)}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
          {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          <span>{title || 'JSON Payload'}</span>
        </div>
        <button
          type="button"
          onClick={handleCopy}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '0.25rem',
            fontSize: '0.7rem',
          }}
          title="Copy JSON"
        >
          {copied ? <Check size={12} style={{ color: '#34d399' }} /> : <Copy size={12} />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>

      {!collapsed && (
        <pre className="code-viewer" style={{ marginTop: '0.25rem' }}>
          <code>{jsonString}</code>
        </pre>
      )}
    </div>
  );
};
