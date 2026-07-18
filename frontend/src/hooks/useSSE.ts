import { useRef, useState } from 'react';
import { useEventStream } from '@/hooks/useEventStream';

const CHAT_EVENT_TYPES = new Set([
  'generate',
  'chat_history',
  'chat',
  'multistep_result',
  'status',
  'error',
  'approval_request',
  'tool',
  'turn_complete',
  'turn_stopped',
  'attachments_ingested',
]);

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
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Error) => void;
  autoReconnect?: boolean;
  maxRetries?: number;
  agentId?: string;
  enabled?: boolean;
}

export function useSSE(options: UseSSEOptions = {}) {
  const {
    onMessage,
    onConnect,
    onDisconnect,
    onError,
    autoReconnect = true,
    maxRetries = 5,
    agentId,
    enabled = true,
  } = options;
  const [lastEvent, setLastEvent] = useState<SSEEvent | null>(null);
  const streamIdRef = useRef<string | null>(null);
  if (!streamIdRef.current) streamIdRef.current = crypto.randomUUID();
  const streamId = streamIdRef.current;
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const streamParams = new URLSearchParams({ stream_id: streamId });
  if (agentId) streamParams.set('agent_id', agentId);
  const path = `/agents/sse?${streamParams.toString()}`;
  const stream = useEventStream<SSEEvent>({
    path,
    enabled,
    autoReconnect,
    maxRetries,
    onConnect,
    onDisconnect,
    onError,
    onEvent: ({ data: event }) => {
      setLastEvent(event);
      if (CHAT_EVENT_TYPES.has(event.type)) onMessageRef.current?.(event);
    },
  });

  return { ...stream, lastEvent, streamId };
}
