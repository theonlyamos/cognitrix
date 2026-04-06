import { useState, useEffect, useCallback, useRef } from 'react';

const API_BACKEND_URI = `${import.meta.env.VITE_BACKEND_URL}/api/v1`;

export interface SSEEvent {
  type: string;
  content?: string;
  action?: string;
  agent_name?: string;
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
  
  const eventSourceRef = useRef<EventSource | null>(null);
  const retryCountRef = useRef(0);
  const retryTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const connect = useCallback(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      setError(new Error('No auth token available'));
      return;
    }

    // Use fetch with ReadableStream for SSE since EventSource doesn't support custom headers
    const url = `${API_BACKEND_URI}/agents/sse`;
    
    fetch(url, {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    }).then(response => {
      if (!response.ok) {
        throw new Error(`SSE connection failed: ${response.status}`);
      }
      
      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('Failed to get reader from response');
      }

      setIsConnected(true);
      setError(null);
      retryCountRef.current = 0;
      onConnect?.();

      const decoder = new TextDecoder();
      let buffer = '';

      const readChunk = () => {
        reader.read().then(({ done, value }) => {
          if (done) {
            console.log('SSE stream ended');
            setIsConnected(false);
            onDisconnect?.();
            
            // Auto reconnect if enabled
            if (autoReconnect && retryCountRef.current < maxRetries) {
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
                    onMessage?.(event);
                  } else if (event.type === 'tool') {
                    onTool?.(event.tool_name as string, event.status as string);
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
        onError?.(err);
      }
    });
  }, [onMessage, onTool, onConnect, onDisconnect, onError, autoReconnect, maxRetries]);

  const disconnect = useCallback(() => {
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsConnected(false);
    onDisconnect?.();
  }, [onDisconnect]);

  const reconnect = useCallback(() => {
    disconnect();
    retryCountRef.current = 0;
    connect();
  }, [connect, disconnect]);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

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