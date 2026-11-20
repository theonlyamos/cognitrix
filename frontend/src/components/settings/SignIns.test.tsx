import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SignInsSettings } from './SignIns';

const { get, post, del, logout } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), del: vi.fn(), logout: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, post, delete: del },
}));
vi.mock('@/context/AppContext', () => ({ useUser: () => ({ logout }) }));

const device = (over = {}) => ({
  id: 's1', name: 'Chrome on Windows', kind: 'browser', method: 'password', approved_by: '', address: '41.66.x.x',
  created_at: '2026-11-20 10:00:00', last_seen_at: '2026-11-20 10:00:00', current: true, ...over,
});

let statusCalls = 0;
beforeEach(() => {
  [get, post, del, logout].forEach((fn) => fn.mockReset());
  statusCalls = 0;
  get.mockImplementation((url: string) => {
    if (url === '/auth/sign-ins') {
      return Promise.resolve({ data: {
        current_listed: true, code_sign_in: true,
        sign_ins: [device(), device({ id: 's2', name: 'Safari on iPhone', current: false, method: 'code', approved_by: 'Chrome on Windows' })],
      } });
    }
    if (url.startsWith('/auth/sign-ins/codes/')) {
      statusCalls += 1;
      return Promise.resolve({ data: statusCalls < 2 ? { status: 'waiting', expires_in: 290 }
        : { status: 'used', sign_in: device({ id: 's3', name: 'Firefox on Linux', current: false, method: 'code' }) } });
    }
    return Promise.resolve({ data: {} });
  });
});

describe('SignInsSettings', () => {
  it('lists devices with how they signed in, and signs one out', async () => {
    del.mockResolvedValue({ data: { ok: true, was_current: false } });
    render(<SignInsSettings />);
    expect(await screen.findByText('Safari on iPhone')).toBeInTheDocument();
    expect(screen.getByText('This device')).toBeInTheDocument();
    expect(screen.getByText('Signed in with a code from Chrome on Windows')).toBeInTheDocument();
    // Only the other device has a Sign out button.
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('/auth/sign-ins/s2'));
    expect(logout).not.toHaveBeenCalled();
  });

  it('signs out all other devices', async () => {
    post.mockResolvedValue({ data: { signed_out: 1 } });
    render(<SignInsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out all others' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/auth/sign-ins/sign-out-others'));
  });

  it('makes a code, shows it with a QR code, then shows the device that used it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    post.mockResolvedValue({ data: { code: 'KXRT-4M9P', expires_in: 300 } });
    del.mockResolvedValue({ data: { ok: true } });
    render(<SignInsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Get a code' }));
    expect(await screen.findByText('KXRT-4M9P')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'QR code to sign in on a phone' }).innerHTML).toContain('<svg');
    await vi.advanceTimersByTimeAsync(4100);
    expect(await screen.findByText('Firefox on Linux')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: "That wasn't me, sign it out" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('/auth/sign-ins/s3'));
    expect(await screen.findByText('Signed out')).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('cancels an unused code when the dialog closes', async () => {
    post.mockResolvedValue({ data: { code: 'KXRT-4M9P', expires_in: 300 } });
    del.mockResolvedValue({ data: { ok: true } });
    render(<SignInsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Get a code' }));
    await screen.findByText('KXRT-4M9P');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('/auth/sign-ins/codes/KXRT-4M9P'));
  });
});
