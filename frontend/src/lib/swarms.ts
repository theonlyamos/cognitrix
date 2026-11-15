import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';

export type WorkerStatus = 'queued' | 'running' | 'reviewing' | 'done' | 'failed' | 'stopped';

export interface SwarmWorker {
  id: string;
  name: string;
  role: string;
  agent_id: string;
  agent_name: string;
  model: string;
  brief: string;
  depends_on: string[];
  may_edit: boolean;
  status: WorkerStatus;
  stalled: boolean;
  chat_id: string;
  summary: string;
  error: string;
  tokens: number;
  review: string;
  changed: number;
  branch: string;
  merged: boolean;
  added_by: string;
  last_step: string;
}

export interface Swarm {
  id: string;
  session_id: string;
  agent_name: string;
  title: string;
  mode: 'fanout' | 'best_of';
  status: 'running' | 'done' | 'stopped';
  stop_reason: string;
  judge: { winner?: string; verdict?: string };
  ledger: { total: number; done: number; failed: number; stopped: number; running: number; queued: number; tokens: number; token_budget: number };
  started_at: string;
  finished_at: string;
  workers: SwarmWorker[];
}

export interface SwarmLimits {
  enabled: boolean;
  concurrency: number;
  max_workers: number;
  token_budget: number;
  auto_start: number;
  ceilings: { concurrency: number; max_workers: number; token_budget: number };
}

export const ENDED: WorkerStatus[] = ['done', 'failed', 'stopped'];

export function tokensLabel(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

/** "2 of 5 done, 1 failed" */
export function progressLabel(swarm: Swarm): string {
  const l = swarm.ledger;
  const parts = [`${l.done} of ${l.total} done`];
  if (l.failed) parts.push(`${l.failed} failed`);
  if (l.stopped) parts.push(`${l.stopped} stopped`);
  return parts.join(', ');
}

/** The swarms of one chat, refreshed every few seconds while any is running
 *  (or while the chat is busy, when one may be about to start). */
export function useChatSwarms(sessionId: string | null, busy: boolean) {
  const [swarms, setSwarms] = useState<Swarm[]>([]);
  const sidRef = useRef(sessionId);
  sidRef.current = sessionId;

  const load = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid) {
      setSwarms([]);
      return;
    }
    try {
      const { data } = await api.get<Swarm[]>('/swarms', { params: { session_id: sid } });
      if (sidRef.current === sid) setSwarms(Array.isArray(data) ? data : []);
    } catch {
      // Keep what is shown; the next refresh tries again.
    }
  }, []);

  useEffect(() => {
    setSwarms([]);
    void load();
  }, [sessionId, load]);

  const running = swarms.some((s) => s.status === 'running');
  useEffect(() => {
    if (!sessionId || !(running || busy)) return;
    const id = window.setInterval(() => void load(), running ? 2500 : 4000);
    return () => window.clearInterval(id);
  }, [sessionId, running, busy, load]);
  // One more look when the agent's turn ends, for a swarm it started at the very end.
  useEffect(() => {
    if (!busy) void load();
  }, [busy, load]);

  const stop = useCallback(async (swarmId: string) => {
    await api.post(`/swarms/${swarmId}/stop`);
    await load();
  }, [load]);
  const stopWorker = useCallback(async (swarmId: string, name: string) => {
    await api.post(`/swarms/${swarmId}/workers/${encodeURIComponent(name)}/stop`);
    await load();
  }, [load]);
  const merge = useCallback(async (swarmId: string, name: string): Promise<string> => {
    const { data } = await api.post<{ merged: boolean; message: string }>(`/swarms/${swarmId}/workers/${encodeURIComponent(name)}/merge`);
    await load();
    return data.message;
  }, [load]);

  return { swarms, reload: load, stop, stopWorker, merge };
}
