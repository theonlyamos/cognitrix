import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const shell = vi.hoisted(() => ({
  on: true,
  saved: null as string | null,
  getSavedSession: vi.fn(),
  saveSession: vi.fn(),
  clearSession: vi.fn(),
}));

vi.mock('@/lib/desktop', () => ({
  isDesktop: () => shell.on,
  desktop: {
    getSavedSession: shell.getSavedSession,
    saveSession: shell.saveSession,
    clearSession: shell.clearSession,
  },
}));

import { clearToken, refreshTokenIfOld, restoreToken, setToken, tokenAge } from '@/lib/session';

function jwtWithIat(iat: number) {
  const body = btoa(JSON.stringify({ sub: 'a@b.com', iat })).replace(/=+$/, '');
  return `h.${body}.s`;
}

describe('sign-in token', () => {
  beforeEach(() => {
    localStorage.clear();
    shell.on = true;
    shell.saved = null;
    shell.getSavedSession.mockReset().mockImplementation(async () => shell.saved);
    shell.saveSession.mockReset().mockImplementation(async (token: string) => { shell.saved = token; });
    shell.clearSession.mockReset().mockImplementation(async () => { shell.saved = null; });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('brings the saved desktop sign-in back when this origin has none', async () => {
    shell.saved = 'saved-token';
    expect(await restoreToken()).toBe('saved-token');
    expect(localStorage.getItem('token')).toBe('saved-token');
  });

  it('hands a sign-in made before the shell kept one to the shell', async () => {
    localStorage.setItem('token', 'local-token');
    expect(await restoreToken()).toBe('local-token');
    expect(shell.saveSession).toHaveBeenCalledWith('local-token');
  });

  it('keeps and clears the shell copy with the local one', () => {
    setToken('t1');
    expect(shell.saveSession).toHaveBeenCalledWith('t1');
    clearToken();
    expect(localStorage.getItem('token')).toBeNull();
    expect(shell.clearSession).toHaveBeenCalled();
  });

  it('leaves the shell alone in a browser', async () => {
    shell.on = false;
    setToken('t1');
    expect(await restoreToken()).toBe('t1');
    expect(shell.saveSession).not.toHaveBeenCalled();
    expect(shell.getSavedSession).not.toHaveBeenCalled();
  });

  it('survives a shell without the session commands', async () => {
    shell.saveSession.mockImplementation(() => { throw new Error('unknown command'); });
    shell.getSavedSession.mockRejectedValue(new Error('unknown command'));
    expect(() => setToken('t1')).not.toThrow();
    expect(await restoreToken()).toBe('t1');
  });

  it('reads the token age from its iat', () => {
    expect(tokenAge(jwtWithIat(1000), 1_060_000)).toBe(60);
    expect(tokenAge('not-a-jwt')).toBeNull();
  });

  it('refreshes an old token and keeps a young one', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ access_token: 'fresh' }) });
    vi.stubGlobal('fetch', fetchMock);

    localStorage.setItem('token', jwtWithIat(Math.floor(Date.now() / 1000) - 60));
    await refreshTokenIfOld('/api/v1');
    expect(fetchMock).not.toHaveBeenCalled();

    const old = jwtWithIat(Math.floor(Date.now() / 1000) - 7 * 3600);
    localStorage.setItem('token', old);
    await refreshTokenIfOld('/api/v1');
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/refresh', expect.objectContaining({
      method: 'POST',
      headers: { Authorization: `Bearer ${old}` },
    }));
    expect(localStorage.getItem('token')).toBe('fresh');
    expect(shell.saved).toBe('fresh');
  });

  it('keeps the old token when the refresh fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const old = jwtWithIat(1);
    localStorage.setItem('token', old);
    await refreshTokenIfOld('/api/v1');
    expect(localStorage.getItem('token')).toBe(old);
  });
});
