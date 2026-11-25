import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';

/**
 * "Auto-approve everything": saved on the account (server side), so it holds
 * in the browser and in the desktop app, whose localStorage is lost when its
 * port changes. While on, every approval card meant for this person is
 * answered Yes for them (see cognitrix/safety/auto_approve_all.py).
 */
let current = false;
let loaded: Promise<void> | null = null;
const listeners = new Set<(on: boolean) => void>();

function publish(on: boolean) {
  current = on;
  listeners.forEach((fn) => fn(on));
}

function load(): Promise<void> {
  if (!loaded) {
    // The old per-browser switch is gone; it only skipped some prompts.
    try { localStorage.removeItem('bypassPermissions'); } catch { /* not stored */ }
    loaded = Promise.resolve()
      .then(() => api.get<{ on: boolean }>('/account/auto-approve'))
      .then((res) => publish(Boolean(res?.data?.on)))
      .catch(() => { loaded = null; });
  }
  return loaded;
}

export async function setAutoApprove(on: boolean): Promise<boolean> {
  const res = await api.put<{ on: boolean }>('/account/auto-approve', { on });
  publish(Boolean(res.data?.on));
  return current;
}

/** For tests. */
export function resetAutoApprove() {
  current = false;
  loaded = null;
}

export function useAutoApprove(): { on: boolean; set: (on: boolean) => Promise<boolean> } {
  const [on, setOn] = useState(current);
  useEffect(() => {
    listeners.add(setOn);
    setOn(current);
    void load();
    return () => { listeners.delete(setOn); };
  }, []);
  const set = useCallback((next: boolean) => setAutoApprove(next), []);
  return { on, set };
}
