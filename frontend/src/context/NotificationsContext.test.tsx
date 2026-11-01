import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NotificationsProvider, useNotifications } from './NotificationsContext';

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({ data: [] }), post: vi.fn() },
  API_BASE: '/api/v1',
}));

vi.mock('@/context/AppContext', () => ({
  useUser: () => ({ user: { id: 'u1', email: 'a@example.com' } }),
}));

const shell = vi.hoisted(() => ({ desktop: true, shown: true }));
const systemNotify = vi.hoisted(() => vi.fn());
vi.mock('@/lib/desktop', () => ({
  isDesktop: () => shell.desktop,
  systemNotify,
  onDesktopEvent: vi.fn().mockResolvedValue(() => {}),
  desktop: { engineStatus: vi.fn().mockResolvedValue(null) },
}));

let ctx: ReturnType<typeof useNotifications>;
function Probe() {
  ctx = useNotifications();
  return (
    <div>
      <span data-testid="toasts">{ctx.toasts.length}</span>
      <span data-testid="history">{ctx.notifications.length}</span>
    </div>
  );
}

function pushApproval() {
  act(() => {
    ctx.push({ event: 'approval', title: 'Approval needed: Bash', body: 'Paused', href: '/inbox' });
  });
}

describe('system notifications', () => {
  beforeEach(() => {
    localStorage.clear();
    systemNotify.mockReset();
    systemNotify.mockImplementation(() => Promise.resolve(shell.shown));
  });

  it('uses a Windows notification instead of the in-app toast in the desktop app', async () => {
    shell.desktop = true;
    shell.shown = true;
    render(<NotificationsProvider><Probe /></NotificationsProvider>);
    pushApproval();
    await waitFor(() => expect(systemNotify).toHaveBeenCalled());
    expect(systemNotify).toHaveBeenCalledWith({ title: 'Approval needed: Bash', body: 'Paused', href: '/inbox', urgent: true });
    await act(async () => {});
    expect(screen.getByTestId('toasts').textContent).toBe('0');
    expect(screen.getByTestId('history').textContent).toBe('1');
  });

  it('falls back to the in-app toast when Windows notifications are off', async () => {
    shell.desktop = true;
    shell.shown = false;
    render(<NotificationsProvider><Probe /></NotificationsProvider>);
    pushApproval();
    await waitFor(() => expect(screen.getByTestId('toasts').textContent).toBe('1'));
  });

  it('keeps the in-app toast on the web', async () => {
    shell.desktop = false;
    shell.shown = true;
    render(<NotificationsProvider><Probe /></NotificationsProvider>);
    pushApproval();
    await waitFor(() => expect(screen.getByTestId('toasts').textContent).toBe('1'));
  });

  it('leaves "engine stopped" to the desktop shell', async () => {
    shell.desktop = true;
    shell.shown = true;
    render(<NotificationsProvider><Probe /></NotificationsProvider>);
    act(() => {
      ctx.push({ event: 'engine', title: 'The local engine stopped', body: 'x', href: '/settings' });
    });
    await waitFor(() => expect(screen.getByTestId('toasts').textContent).toBe('1'));
    expect(systemNotify).not.toHaveBeenCalled();
  });
});
