import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UserProvider, useUser } from '@/context/AppContext';

vi.mock('@/lib/desktop', () => ({ isDesktop: () => false, desktop: {} }));

function Probe() {
  const { user, isLoading, waitingForServer } = useUser();
  return (
    <div>
      <span data-testid="state">{isLoading ? 'loading' : user ? `user:${user.email}` : 'signed-out'}</span>
      <span data-testid="waiting">{String(waitingForServer)}</span>
    </div>
  );
}

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

describe('UserProvider', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('keeps the sign-in while the engine is unreachable, then signs in', async () => {
    vi.useFakeTimers();
    localStorage.setItem('token', 'tok');
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({}) })
      .mockResolvedValue(ok({ name: 'Amos', email: 'a@b.com' }));
    vi.stubGlobal('fetch', fetchMock);

    render(<UserProvider><Probe /></UserProvider>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByTestId('state').textContent).toBe('loading');
    expect(localStorage.getItem('token')).toBe('tok');

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByTestId('waiting').textContent).toBe('true');
    expect(localStorage.getItem('token')).toBe('tok');

    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByTestId('state').textContent).toBe('user:a@b.com');
    expect(screen.getByTestId('waiting').textContent).toBe('false');
    expect(localStorage.getItem('token')).toBe('tok');
  });

  it('signs out when the server rejects the token', async () => {
    localStorage.setItem('token', 'tok');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }));
    render(<UserProvider><Probe /></UserProvider>);
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('signed-out'));
    expect(localStorage.getItem('token')).toBeNull();
  });

  it('signs out after a 401 elsewhere only when the token really ended', async () => {
    localStorage.setItem('token', 'tok');
    const fetchMock = vi.fn().mockResolvedValue(ok({ name: 'Amos', email: 'a@b.com' }));
    vi.stubGlobal('fetch', fetchMock);
    render(<UserProvider><Probe /></UserProvider>);
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('user:a@b.com'));

    fetchMock.mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });
    act(() => { window.dispatchEvent(new Event('cognitrix:auth-expired')); });
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('signed-out'));
    expect(localStorage.getItem('token')).toBeNull();
  });
});
