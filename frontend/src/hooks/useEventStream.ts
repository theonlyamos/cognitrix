import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from '@/lib/api';
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
    maxRetries = 5,
    initialLastEventId,
  } = options;
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
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
    setIsConnected(false);
    onDisconnectRef.current?.();
  }, []);

  const connect = useCallback(() => {
    if (!enabled || !path) return;
    const token = localStorage.getItem('token');
    if (!token) {
      const missing = new Error('No auth token available');
      setError(missing);
      onErrorRef.current?.(missing);
      return;
    }

    abortRef.current?.abort();
    if (readerRef.current) cancelReader(readerRef.current);
    if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);

    const controller = new AbortController();
    abortRef.current = controller;
    const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
    if (lastEventIdRef.current) headers['Last-Event-ID'] = lastEventIdRef.current;

    const scheduleReconnect = (streamError?: Error) => {
      if (autoReconnect && !controller.signal.aborted && retryCountRef.current < maxRetries) {
        const delay = Math.min(1000 * (2 ** retryCountRef.current), 30000);
        retryTimeoutRef.current = setTimeout(() => {
          retryCountRef.current += 1;
          connectRef.current();
        }, delay);
      } else if (streamError) {
        setError(streamError);
        onErrorRef.current?.(streamError);
      }
    };

    void fetch(`${API_BASE}${path}`, { headers, signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`SSE connection failed: ${response.status}`);
        const reader = response.body?.getReader();
        if (!reader) throw new Error('Failed to get reader from response');

        readerRef.current = reader;
        setIsConnected(true);
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
                try {
                  const data = JSON.parse(frame.data) as T;
                  if (frame.id) lastEventIdRef.current = frame.id;
                  retryCountRef.current = 0;
                  onEventRef.current?.({ id: frame.id, event: frame.event, data });
                } catch (parseError) {
                  console.error('Failed to parse SSE event:', parseError);
                }
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
    connect();
  }, [connect, disconnect]);

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
    error,
    connect,
    disconnect,
    reconnect,
    clearError,
    lastEventId: lastEventIdRef.current,
  };
}
