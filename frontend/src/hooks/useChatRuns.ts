import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';

export interface ChatRunSummary {
  run_id: string;
  session_id: string;
  agent_id: string;
  status: string;
  started_at: string;
  prompt?: string;
}

const BUSY_POLL_MS = 3_000;
const IDLE_POLL_MS = 15_000;

/** Fetch the conversations of one agent whose last message is still being worked on. */
export async function fetchChatRuns(agentId: string): Promise<ChatRunSummary[]> {
  const res = await api.get('/agents/runs', { params: { agent_id: agentId } });
  const runs = (res.data as { runs?: ChatRunSummary[] } | undefined)?.runs;
  return Array.isArray(runs) ? runs : [];
}

/**
 * Conversations of one agent that are still running on the server, even ones
 * the user has navigated away from. Polls faster while anything is running.
 */
export function useChatRuns(agentId: string | null | undefined) {
  const [runs, setRuns] = useState<ChatRunSummary[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const agentRef = useRef(agentId);
  agentRef.current = agentId;

  const refresh = useCallback(async () => {
    const id = agentRef.current;
    if (!id) {
      setRuns([]);
      return [];
    }
    try {
      const next = await fetchChatRuns(id);
      if (agentRef.current === id) setRuns(next);
      return next;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const next = typeof document !== 'undefined' && document.hidden ? null : await refresh();
      if (cancelled) return;
      const delay = next && next.length > 0 ? BUSY_POLL_MS : IDLE_POLL_MS;
      timerRef.current = setTimeout(() => void tick(), delay);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
    };
  }, [agentId, refresh]);

  const runningIds = new Set(runs.map((run) => run.session_id));
  return { runs, runningIds, refresh };
}
