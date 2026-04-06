import { useState, useEffect, useCallback, useRef } from 'react';

const API_BACKEND_URI = `${import.meta.env.VITE_BACKEND_URL}/api/v1`;

export interface SSEEvent {
  type: string;
  content?: string;
  action?: string;
  agent_name?: string;
  tool_name?: string;
  status?: string;
  [key: string]: unknown;
}

interface UseSSEOptions {
  onMessage?: (event: SSEEvent) => void;
  onTool?: (toolName: string, status: string) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Error) => void;
  autoReconnect?: boolean;
  maxRetries?: number;
}

export function useSSE(options: UseSSEOptions = {}) {
  const {
    onMessage,
    onTool,
    onConnect,
    onDisconnect,
    onError,
    autoReconnect = true,
    maxRetries = 5,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<SSEEvent | null>(null);
  const [error, setError] = useState<Error | null>(null);

  // Use refs to store callbacks to prevent re-render triggering reconnect
  const onMessageRef = useRef(onMessage);
  const onToolRef = useRef(onTool);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onErrorRef = useRef(onError);

  // Update refs when callbacks change
  onMessageRef.current = onMessage;
  onToolRef.current = onTool;
  onConnectRef.current = onConnect;
  onDisconnectRef.current = onDisconnect;
  onErrorRef.current = onError;

  const readerRef = useRef<ReadableStreamReader<Uint8Array> | null>(null);
  const retryCountRef = useRef(0);
  const retryTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const connect = useCallback(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      setError(new Error('No auth token available'));
      return;
    }

    // Cancel any existing connection
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    if (readerRef.current) {
      readerRef.current.cancel();
    }
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    const url = `${API_BACKEND_URI}/agents/sse`;

    fetch(url, {
      headers: {
        'Authorization': `Bearer ${token}`
      },
      signal: controller.signal
    }).then(response => {
      if (!response.ok) {
        throw new Error(`SSE connection failed: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('Failed to get reader from response');
      }

      readerRef.current = reader;
      setIsConnected(true);
      setError(null);
      retryCountRef.current = 0;
      onConnectRef.current?.();

      const decoder = new TextDecoder();
      let buffer = '';

      const readChunk = () => {
        if (controller.signal.aborted) {
          return;
        }

        reader.read().then(({ done, value }) => {
          if (done || controller.signal.aborted) {
            console.log('SSE stream ended');
            setIsConnected(false);
            onDisconnectRef.current?.();

            // Auto reconnect if enabled and not intentionally aborted
            if (autoReconnect && !controller.signal.aborted && retryCountRef.current < maxRetries) {
              const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
              retryTimeoutRef.current = setTimeout(() => {
                retryCountRef.current++;
                connect();
              }, delay);
            }
            return;
          }

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data:')) {
              const data = line.slice(5).trim();
              if (data) {
                try {
                  const event = JSON.parse(data) as SSEEvent;
                  setLastEvent(event);

                  if (event.type === 'generate' || event.type === 'chat_history' || event.type === 'chat') {
                    onMessageRef.current?.(event);
                  } else if (event.type === 'tool') {
                    onToolRef.current?.(event.tool_name as string, event.status as string);
                  }
                } catch (err) {
                  console.error('Failed to parse SSE event:', err);
                }
              }
            }
          }

          readChunk();
        });
      };

      readChunk();
    }).catch(err => {
      if (controller.signal.aborted) {
        return;
      }
      console.error('SSE fetch error:', err);
      setIsConnected(false);

      if (autoReconnect && retryCountRef.current < maxRetries) {
        const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
        retryTimeoutRef.current = setTimeout(() => {
          retryCountRef.current++;
          connect();
        }, delay);
      } else {
        setError(err);
        onErrorRef.current?.(err);
      }
    });
  }, [autoReconnect, maxRetries]);

  const disconnect = useCallback(() => {
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    if (readerRef.current) {
      readerRef.current.cancel();
      readerRef.current = null;
    }
    setIsConnected(false);
    onDisconnectRef.current?.();
  }, []);

  const reconnect = useCallback(() => {
    disconnect();
    retryCountRef.current = 0;
    connect();
  }, [connect, disconnect]);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  // Only run connect/disconnect on mount/unmount, not on every render
  useEffect(() => {
    connect();
    return () => disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    isConnected,
    lastEvent,
    error,
    connect,
    disconnect,
    reconnect,
    clearError,
  };
}