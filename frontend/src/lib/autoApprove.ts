import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useOptionalUser } from '@/context/AppContext';

/**
 * "Auto-approve everything": saved on the account (server side), so it holds
 * in the browser and in the desktop app, whose localStorage is lost when its
 * port changes. While on, every approval card meant for this person is
 * answered Yes for them (see cognitrix/safety/auto_approve_all.py).
 */
let current = false;
let loaded: Promise<void> | null = null;
// Whose setting `current` is: signing in as someone else (no page reload)
// fetches theirs.
let loadedFor: string | null = null;
const listeners = new Set<(on: boolean) => void>();

function publish(on: boolean) {
  current = on;
  listeners.forEach((fn) => fn(on));
}

function load(who: string): Promise<void> {
  if (loadedFor !== who) {
    loadedFor = who;
    loaded = null;
    publish(false);
  }
  if (!loaded) {
    // The old per-browser switch is gone; it only skipped some prompts.
    try { localStorage.removeItem('bypassPermissions'); } catch { /* not stored */ }
    loaded = Promise.resolve()
      .then(() => api.get<{ on: boolean }>('/account/auto-approve'))
      .then((res) => { if (loadedFor === who) publish(Boolean(res?.data?.on)); })
      .then(() => undefined, () => { if (loadedFor === who) loaded = null; });
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
  loadedFor = null;
}

export function useAutoApprove(): { on: boolean; set: (on: boolean) => Promise<boolean> } {
  const user = useOptionalUser();
  const who = String(user?.id || user?.email || '');
  const [on, setOn] = useState(loadedFor === who ? current : false);
  useEffect(() => {
    listeners.add(setOn);
    void load(who);
    setOn(current);
    return () => { listeners.delete(setOn); };
  }, [who]);
  const set = useCallback((next: boolean) => setAutoApprove(next), []);
  return { on, set };
}
