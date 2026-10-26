import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../lib/api';
import { SSEJobUpdateData } from '../types/api';

export type StreamConnectionStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected' | 'polling_fallback';

interface UseEventStreamOptions {
  enabled?: boolean;
  onJobUpdate?: (update: SSEJobUpdateData) => void;
  onRefreshNeeded?: () => void;
}

export function useEventStream({
  enabled = true,
  onJobUpdate,
  onRefreshNeeded,
}: UseEventStreamOptions = {}) {
  const [status, setStatus] = useState<StreamConnectionStatus>('disconnected');
  const [lastEventTime, setLastEventTime] = useState<Date | null>(null);
  const [retryCount, setRetryCount] = useState<number>(0);

  const eventSourceRef = useRef<EventSource | null>(null);
  const retryTimeoutRef = useRef<any>(null);
  const isMountedRef = useRef<boolean>(true);

  const maxRetries = 5;

  const connect = useCallback(() => {
    if (!enabled || !isMountedRef.current) return;

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    setStatus('connecting');
    const url = api.getEventStreamUrl();
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => {
      if (!isMountedRef.current) return;
      setStatus('connected');
      setRetryCount(0);
      setLastEventTime(new Date());
      // Reconnected: trigger state refresh from REST API to ensure no missed transitions
      if (onRefreshNeeded) {
        onRefreshNeeded();
      }
    };

    es.addEventListener('connected', () => {
      if (!isMountedRef.current) return;
      setStatus('connected');
      setLastEventTime(new Date());
    });

    es.addEventListener('job_update', (e: MessageEvent) => {
      if (!isMountedRef.current) return;
      setLastEventTime(new Date());
      try {
        const parsed: SSEJobUpdateData = JSON.parse(e.data);
        if (onJobUpdate) {
          onJobUpdate(parsed);
        }
        if (onRefreshNeeded) {
          onRefreshNeeded();
        }
      } catch {
        // Ignored unparsable payload
      }
    });

    es.onerror = () => {
      if (!isMountedRef.current) return;
      es.close();
      eventSourceRef.current = null;

      setRetryCount((prev) => {
        const nextCount = prev + 1;
        if (nextCount > maxRetries) {
          // Bounded backoff exhausted: fall back to polling mode
          setStatus('polling_fallback');
          return nextCount;
        }

        setStatus('reconnecting');
        // Exponential backoff with ceiling (1s, 2s, 4s, 8s, 16s)
        const delay = Math.min(16000, 1000 * Math.pow(2, prev));
        clearTimeout(retryTimeoutRef.current);
        retryTimeoutRef.current = setTimeout(() => {
          if (isMountedRef.current) {
            connect();
          }
        }, delay);

        return nextCount;
      });
    };
  }, [enabled, onJobUpdate, onRefreshNeeded]);

  useEffect(() => {
    isMountedRef.current = true;
    if (enabled) {
      connect();
    } else {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setStatus('disconnected');
    }

    return () => {
      isMountedRef.current = false;
      clearTimeout(retryTimeoutRef.current);
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [enabled, connect]);

  const reconnect = useCallback(() => {
    clearTimeout(retryTimeoutRef.current);
    setRetryCount(0);
    connect();
  }, [connect]);

  return {
    status,
    lastEventTime,
    retryCount,
    reconnect,
  };
}
