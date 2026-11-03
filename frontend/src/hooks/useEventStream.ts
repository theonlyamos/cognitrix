import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from '@/lib/api';
import { onDesktopEvent, type EngineStatus } from '@/lib/desktop';
import { AUTH_EXPIRED_EVENT } from '@/lib/session';
import { consumeSSE } from '@/lib/sse';

export interface JSONSSEFrame<T> {
  id?: string;
  event?: string;
  data: T;
}

export interface UseEventStreamOptions<T> {
  path: string | null;
  onEvent?: (frame: JSONSSEFrame<T>) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Error) => void;
  enabled?: boolean;
  autoReconnect?: boolean;
  maxRetries?: number;
  initialLastEventId?: string;
}

function cancelReader(reader: ReadableStreamReader<Uint8Array>) {
  const cancellation = reader.cancel();
  if (cancellation && typeof cancellation.catch === 'function') {
    void cancellation.catch(() => undefined);
  }
}

export function useEventStream<T>(options: UseEventStreamOptions<T>) {
  const {
    path,
    onEvent,
    onConnect,
    onDisconnect,
    onError,
    enabled = true,
    autoReconnect = true,
    maxRetries = 8,
    initialLastEventId,
  } = options;
  const [isConnected, setIsConnected] = useState(false);
  /** A connection attempt is under way or a retry is scheduled. */
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  // Where the stream stands, for the wake-up triggers below: only a stream
  // that's waiting to retry or has given up gets kicked.
  const phaseRef = useRef<'idle' | 'connecting' | 'connected' | 'waiting' | 'gave_up'>('idle');
  const onEventRef = useRef(onEvent);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onErrorRef = useRef(onError);
  onEventRef.current = onEvent;
  onConnectRef.current = onConnect;
  onDisconnectRef.current = onDisconnect;
  onErrorRef.current = onError;

  const readerRef = useRef<ReadableStreamReader<Uint8Array> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const retryCountRef = useRef(0);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastEventIdRef = useRef(initialLastEventId);
  const connectRef = useRef<() => void>(() => undefined);

  const disconnect = useCallback(() => {
    if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
    retryTimeoutRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    if (readerRef.current) {
      cancelReader(readerRef.current);
      readerRef.current = null;
    }
    phaseRef.current = 'idle';
    setIsConnected(false);
    setIsConnecting(false);
    onDisconnectRef.current?.();
  }, []);

  const connect = useCallback(() => {
    if (!enabled || !path) return;
    const token = localStorage.getItem('token');
    if (!token) {
      const missing = new Error('No auth token available');
      phaseRef.current = 'gave_up';
      setIsConnecting(false);
      setError(missing);
      onErrorRef.current?.(missing);
      return;
    }

    abortRef.current?.abort();
    if (readerRef.current) cancelReader(readerRef.current);
    if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);

    const controller = new AbortController();
    abortRef.current = controller;
    phaseRef.current = 'connecting';
    setIsConnecting(true);
    const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
    if (lastEventIdRef.current) headers['Last-Event-ID'] = lastEventIdRef.current;

    const scheduleReconnect = (streamError?: Error) => {
      if (controller.signal.aborted) return;
      if (autoReconnect && retryCountRef.current < maxRetries) {
        const delay = Math.min(1000 * (2 ** retryCountRef.current), 30000);
        phaseRef.current = 'waiting';
        setIsConnecting(true);
        retryTimeoutRef.current = setTimeout(() => {
          retryCountRef.current += 1;
          connectRef.current();
        }, delay);
        return;
      }
      phaseRef.current = 'gave_up';
      setIsConnecting(false);
      if (streamError) {
        setError(streamError);
        onErrorRef.current?.(streamError);
      }
    };

    void fetch(`${API_BASE}${path}`, { headers, signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
          throw new Error(`SSE connection failed: ${response.status}`);
        }
        const reader = response.body?.getReader();
        if (!reader) throw new Error('Failed to get reader from response');

        readerRef.current = reader;
        phaseRef.current = 'connected';
        setIsConnected(true);
        setIsConnecting(false);
        setError(null);
        onConnectRef.current?.();

        const decoder = new TextDecoder();
        let buffer = '';
        const readChunk = () => {
          if (controller.signal.aborted) return;
          void reader.read()
            .then(({ done, value }) => {
              if (done || controller.signal.aborted) {
                if (!controller.signal.aborted) {
                  setIsConnected(false);
                  onDisconnectRef.current?.();
                  scheduleReconnect();
                }
                return;
              }

              buffer += decoder.decode(value, { stream: true });
              const parsed = consumeSSE(buffer);
              buffer = parsed.rest;
              for (const frame of parsed.frames) {
                if (!frame.data.trim()) continue;
                let data: T;
                try {
                  data = JSON.parse(frame.data) as T;
                } catch (parseError) {
                  console.error('Failed to parse SSE event:', parseError);
                  continue;
                }

                onEventRef.current?.({ id: frame.id, event: frame.event, data });
                if (frame.id !== undefined) lastEventIdRef.current = frame.id;
                retryCountRef.current = 0;
              }
              readChunk();
            })
            .catch((caught: unknown) => {
              if (controller.signal.aborted) return;
              const streamError = caught instanceof Error ? caught : new Error(String(caught));
              setIsConnected(false);
              scheduleReconnect(streamError);
            });
        };
        readChunk();
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        const streamError = caught instanceof Error ? caught : new Error(String(caught));
        setIsConnected(false);
        scheduleReconnect(streamError);
      });
  }, [autoReconnect, enabled, maxRetries, path]);

  connectRef.current = connect;

  const reconnect = useCallback(() => {
    disconnect();
    retryCountRef.current = 0;
    setError(null);
    connect();
  }, [connect, disconnect]);

  // Try again straight away when there's reason to think it will work: the
  // network came back, the window was shown again, or the desktop engine
  // finished (re)starting. This is what reconnects after the retries ran out.
  useEffect(() => {
    if (!enabled || !path || !autoReconnect) return;
    const kick = () => {
      if (phaseRef.current !== 'waiting' && phaseRef.current !== 'gave_up') return;
      retryCountRef.current = 0;
      setError(null);
      connectRef.current();
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible') kick();
    };
    window.addEventListener('online', kick);
    window.addEventListener('focus', kick);
    document.addEventListener('visibilitychange', onVisible);
    let stopEngine: (() => void) | undefined;
    let live = true;
    void onDesktopEvent<EngineStatus>('engine-status', (status) => {
      if (status.status === 'running') kick();
    }).then((unlisten) => {
      if (live) stopEngine = unlisten;
      else unlisten();
    }).catch(() => undefined);
    return () => {
      live = false;
      window.removeEventListener('online', kick);
      window.removeEventListener('focus', kick);
      document.removeEventListener('visibilitychange', onVisible);
      stopEngine?.();
    };
  }, [autoReconnect, enabled, path]);

  const clearError = useCallback(() => setError(null), []);

  useEffect(() => {
    lastEventIdRef.current = initialLastEventId;
    retryCountRef.current = 0;
    if (!enabled || !path) return;
    connect();
    return disconnect;
  }, [connect, disconnect, enabled, initialLastEventId, path]);

  return {
    isConnected,
    isConnecting,
    error,
    connect,
    disconnect,
    reconnect,
    clearError,
    lastEventId: lastEventIdRef.current,
  };
}
