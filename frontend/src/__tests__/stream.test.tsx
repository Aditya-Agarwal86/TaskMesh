import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useEventStream } from '../hooks/useEventStream';

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, ((event: any) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(event: string, callback: (event: any) => void) {
    if (!this.listeners[event]) {
      this.listeners[event] = [];
    }
    this.listeners[event].push(callback);
  }

  removeEventListener(event: string, callback: (event: any) => void) {
    if (this.listeners[event]) {
      this.listeners[event] = this.listeners[event].filter((cb) => cb !== callback);
    }
  }

  close() {
    // closed
  }

  emit(event: string, data: any) {
    if (this.listeners[event]) {
      for (const cb of this.listeners[event]) {
        cb({ data: JSON.stringify(data) });
      }
    }
  }
}

describe('useEventStream hook', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    (window as any).EventSource = MockEventSource;
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('connects to event stream and updates status on open', () => {
    const onJobUpdate = vi.fn();
    const onRefreshNeeded = vi.fn();

    const { result } = renderHook(() =>
      useEventStream({
        enabled: true,
        onJobUpdate,
        onRefreshNeeded,
      })
    );

    expect(result.current.status).toBe('connecting');
    expect(MockEventSource.instances.length).toBe(1);

    const es = MockEventSource.instances[0];
    act(() => {
      es.onopen!();
    });

    expect(result.current.status).toBe('connected');
    expect(onRefreshNeeded).toHaveBeenCalled();
  });

  it('receives job_update events and invokes callbacks', () => {
    const onJobUpdate = vi.fn();
    const onRefreshNeeded = vi.fn();

    renderHook(() =>
      useEventStream({
        enabled: true,
        onJobUpdate,
        onRefreshNeeded,
      })
    );

    const es = MockEventSource.instances[0];
    act(() => {
      es.onopen!();
    });

    act(() => {
      es.emit('job_update', {
        job_id: 'job-12345',
        status: 'RUNNING',
        task_type: 'sleep',
      });
    });

    expect(onJobUpdate).toHaveBeenCalledWith({
      job_id: 'job-12345',
      status: 'RUNNING',
      task_type: 'sleep',
    });
    expect(onRefreshNeeded).toHaveBeenCalledTimes(2); // once on open, once on job_update
  });


  it('falls back to polling when max retries exceeded', () => {
    const { result } = renderHook(() =>
      useEventStream({
        enabled: true,
      })
    );

    // Trigger errors sequentially up to maxRetries (5)
    for (let i = 0; i <= 5; i++) {
      const es = MockEventSource.instances[MockEventSource.instances.length - 1];
      act(() => {
        es.onerror!();
      });
      // advance timer for backoff
      act(() => {
        vi.advanceTimersByTime(20000);
      });
    }

    expect(result.current.status).toBe('polling_fallback');
  });
});
