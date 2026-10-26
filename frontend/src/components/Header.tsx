import React from 'react';
import { RefreshCw, Radio, Wifi, WifiOff, AlertTriangle } from 'lucide-react';
import { StreamConnectionStatus } from '../hooks/useEventStream';

interface HeaderProps {
  title: string;
  onRefresh: () => void;
  isRefreshing?: boolean;
  lastUpdated?: Date;
  streamStatus?: StreamConnectionStatus;
  lastEventTime?: Date | null;
  onReconnect?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  title,
  onRefresh,
  isRefreshing,
  lastUpdated,
  streamStatus = 'connected',
  lastEventTime,
  onReconnect,
}) => {

  const renderStreamBadge = () => {
    switch (streamStatus) {
      case 'connected':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
              fontSize: '0.75rem',
              color: '#34d399',
              background: 'rgba(52, 211, 153, 0.1)',
              padding: '0.25rem 0.55rem',
              borderRadius: 4,
              border: '1px solid rgba(52, 211, 153, 0.25)',
              fontFamily: 'var(--font-mono)',
            }}
            title="Real-time Server-Sent Events stream connected"
          >
            <Wifi size={13} />
            <span>CONNECTED (LIVE)</span>
          </span>
        );
      case 'connecting':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
              fontSize: '0.75rem',
              color: '#60a5fa',
              background: 'rgba(96, 165, 250, 0.1)',
              padding: '0.25rem 0.55rem',
              borderRadius: 4,
              border: '1px solid rgba(96, 165, 250, 0.25)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            <Radio size={13} className="pulse-icon" />
            <span>CONNECTING</span>
          </span>
        );
      case 'reconnecting':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
              fontSize: '0.75rem',
              color: '#fbbf24',
              background: 'rgba(251, 191, 36, 0.1)',
              padding: '0.25rem 0.55rem',
              borderRadius: 4,
              border: '1px solid rgba(251, 191, 36, 0.25)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            <Radio size={13} className="pulse-icon" />
            <span>RECONNECTING...</span>
          </span>
        );
      case 'polling_fallback':
        return (
          <button
            onClick={onReconnect}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
              fontSize: '0.75rem',
              color: '#fbbf24',
              background: 'rgba(251, 191, 36, 0.1)',
              padding: '0.25rem 0.55rem',
              borderRadius: 4,
              border: '1px solid rgba(251, 191, 36, 0.3)',
              cursor: 'pointer',
              fontFamily: 'var(--font-mono)',
            }}
            title="Stream disconnected. Using polling fallback. Click to retry stream."
          >
            <AlertTriangle size={13} />
            <span>POLLING FALLBACK (RECONNECT)</span>
          </button>
        );
      default:
        return (
          <button
            onClick={onReconnect}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
              fontSize: '0.75rem',
              color: '#f87171',
              background: 'rgba(248, 113, 113, 0.1)',
              padding: '0.25rem 0.55rem',
              borderRadius: 4,
              border: '1px solid rgba(248, 113, 113, 0.3)',
              cursor: 'pointer',
              fontFamily: 'var(--font-mono)',
            }}
            title="Stream disconnected. Click to reconnect."
          >
            <WifiOff size={13} />
            <span>OFFLINE (RECONNECT)</span>
          </button>
        );
    }
  };

  return (
    <header className="top-header">
      <div className="header-title-section">
        <h1 className="header-title">{title}</h1>
      </div>

      <div className="header-controls">
        {renderStreamBadge()}

        {(() => {
          const updateTime = lastEventTime || lastUpdated;
          if (!updateTime) return null;

          const isStale =
            streamStatus === 'disconnected' ||
            streamStatus === 'reconnecting' ||
            streamStatus === 'polling_fallback' ||
            Date.now() - updateTime.getTime() > 30000;

          return (
            <span
              style={{
                fontSize: '0.75rem',
                color: isStale ? '#fbbf24' : 'var(--text-muted)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.3rem',
              }}
              title={isStale ? 'Data may be stale. Check stream status or refresh manually.' : 'Latest successful telemetry update'}
            >
              {isStale && <span style={{ width: 6, height: 6, borderRadius: '50%', backgroundColor: '#fbbf24' }} />}
              <span>{isStale ? 'Stale (Updated ' : 'Updated '}</span>
              <span>{updateTime.toLocaleTimeString()}</span>
              {isStale && <span>)</span>}
            </span>
          );
        })()}

        <button
          className="btn btn-secondary btn-sm"
          onClick={onRefresh}
          disabled={isRefreshing}
          title="Manual refresh"
        >
          <RefreshCw size={14} style={{ animation: isRefreshing ? 'spin 1s linear infinite' : undefined }} />
          <span>Refresh</span>
        </button>
      </div>
    </header>
  );
};

