import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Inbox from './Inbox';
import type { InboxItem } from '@/context/NotificationsContext';

const ctx = vi.hoisted(() => ({
  inbox: [] as unknown[],
  openInboxItem: vi.fn(),
  refreshInbox: vi.fn(async () => {}),
}));

vi.mock('@/context/NotificationsContext', () => ({
  useNotifications: () => ({ inbox: ctx.inbox, inboxLoaded: true, refreshInbox: ctx.refreshInbox, openInboxItem: ctx.openInboxItem, unread: 0 }),
}));
vi.mock('@/pages/Notifications', () => ({ RecentNotifications: () => null }));

const alert = { kind: 'notification', id: 'n1', title: 'Backup finished', message: 'All 3 folders copied.', task_id: null, session_id: null, link: null, created_at: '2026-11-27T10:00:00Z' };
const approval = { kind: 'approval', source: 'chat', id: 'ap1', tool_name: 'Shell', agent_name: 'Ada', params: { command: 'ls' }, created_at: '2026-11-27T11:00:00Z' };

function renderInbox(path = '/inbox') {
  return render(<MemoryRouter initialEntries={[path]}><Inbox /></MemoryRouter>);
}

describe('Inbox', () => {
  beforeEach(() => {
    ctx.inbox = [approval, alert];
    ctx.openInboxItem.mockReset();
    ctx.refreshInbox.mockClear();
  });

  it('dismisses an alert when it is opened and keeps showing it', () => {
    const view = renderInbox();
    fireEvent.click(screen.getByRole('button', { name: /Backup finished/ }));
    expect(ctx.openInboxItem).toHaveBeenCalledWith(alert);
    // The context drops the read alert from the list; the page keeps it on screen.
    ctx.inbox = [approval];
    view.rerender(<MemoryRouter initialEntries={['/inbox']}><Inbox /></MemoryRouter>);
    expect(screen.getByText('All 3 folders copied.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Backup finished/ })).toHaveAttribute('aria-current', 'true');
  });

  it('leaves an approval waiting when it is opened', () => {
    renderInbox();
    fireEvent.click(screen.getByRole('button', { name: /Shell/ }));
    expect(ctx.openInboxItem).not.toHaveBeenCalled();
  });

  it('opens the alert named in the link', () => {
    renderInbox('/inbox?item=n1');
    expect(ctx.openInboxItem).toHaveBeenCalledWith(alert as InboxItem);
    expect(screen.getByText('All 3 folders copied.')).toBeInTheDocument();
  });
});
