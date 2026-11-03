/**
 * The sign-in token. It lives in localStorage, where every request reads it,
 * and in the desktop app the shell also keeps a copy in the OS keychain:
 * localStorage belongs to one origin (the engine's port), so a port change or
 * a cleared webview would otherwise sign you out on the next start.
 */
import { desktop, isDesktop } from '@/lib/desktop';

const KEY = 'token';
/** Fired when the server rejects the token, so the app can sign out. */
export const AUTH_EXPIRED_EVENT = 'cognitrix:auth-expired';

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** Run a shell call without letting it break sign-in (an older shell may
 *  not have the command). */
function quietly(call: () => Promise<unknown> | null | undefined): void {
  try {
    void Promise.resolve(call()).catch(() => undefined);
  } catch {
    /* ignore */
  }
}

export function setToken(token: string): void {
  localStorage.setItem(KEY, token);
  if (isDesktop()) quietly(() => desktop.saveSession(token));
}

export function clearToken(): void {
  localStorage.removeItem(KEY);
  if (isDesktop()) quietly(() => desktop.clearSession());
}

/** Desktop app: put the saved token back when this origin has none, and
 *  save the one it has when the shell hasn't got it yet (signed in before
 *  the shell kept a copy). Returns the token to use. */
export async function restoreToken(): Promise<string | null> {
  const local = getToken();
  if (!isDesktop()) return local;
  try {
    const saved = await desktop.getSavedSession();
    if (!local && saved) {
      localStorage.setItem(KEY, saved);
      return saved;
    }
    if (local && local !== saved) await desktop.saveSession(local);
  } catch {
    /* an older shell without these commands */
  }
  return getToken();
}

/** Seconds since the token was issued, or null if it can't be read. */
export function tokenAge(token: string, now = Date.now()): number | null {
  try {
    const part = token.split('.')[1];
    if (!part) return null;
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '='));
    const iat = (JSON.parse(json) as { iat?: number }).iat;
    return typeof iat === 'number' ? now / 1000 - iat : null;
  } catch {
    return null;
  }
}

/** Swap the token for a fresh one once it's a few hours old, so a session
 *  that's in use never reaches its expiry. Failures are ignored: the old
 *  token keeps working until then. */
export async function refreshTokenIfOld(apiBase: string, minAgeSeconds = 6 * 60 * 60): Promise<void> {
  const token = getToken();
  if (!token) return;
  const age = tokenAge(token);
  if (age !== null && age < minAgeSeconds) return;
  try {
    const response = await fetch(`${apiBase}/auth/refresh`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) return;
    const data = (await response.json()) as { access_token?: string };
    // Signed out (or in as someone else) meanwhile: keep that.
    if (data.access_token && getToken() === token) setToken(data.access_token);
  } catch {
    /* engine unreachable; try again later */
  }
}
