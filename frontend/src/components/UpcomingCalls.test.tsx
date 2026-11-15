import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { UpcomingCalls, type CalendarCall, type CalendarCalls } from './UpcomingCalls';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api,
}));

const call = (over: Partial<CalendarCall>): CalendarCall => ({
  id: 'c1', title: 'Vendor review', starts_at: '2026-11-16 09:30:00', ends_at: '2026-11-16 10:00:00',
  url: 'https://meet.google.com/abc', platform: 'meet', organizer: 'boss@acme.test', organised_by_me: false,
  series: true, status: 'suggested', decided_by: '', meeting_id: '', error: '', ...over,
});

const list = (calls: CalendarCall[], connected = true): CalendarCalls => ({
  connected,
  settings: { enabled: true, agent_id: '', auto_join: 'ask', never_words: '', remind_minutes: 15, series_choices: 0, checked_at: '', error: '' },
  calls,
});

const show = (onOpenMeeting = vi.fn()) => render(
  <MemoryRouter><UpcomingCalls agents={[{ id: 'a1', name: 'Ada' }]} onOpenMeeting={onOpenMeeting} /></MemoryRouter>,
);

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
});

describe('UpcomingCalls', () => {
  it('asks to connect Google Calendar when it is not connected', async () => {
    api.get.mockResolvedValue({ data: list([], false) });
    show();
    expect(await screen.findByRole('link', { name: /Connected apps/ })).toHaveAttribute('href', '/settings/apps');
  });

  it('sends an agent only when the person taps, and opens the call', async () => {
    api.get.mockResolvedValue({ data: list([call({}), call({ id: 'c2', title: 'Lunch', series: false, status: 'skipped', decided_by: 'rule' })]) });
    api.post.mockResolvedValue({ data: list([call({ status: 'joined', meeting_id: 'm9' })]) });
    const onOpen = vi.fn();
    show(onOpen);
    const row = (await screen.findByText('Vendor review')).closest('li')!;
    expect(row).toHaveTextContent('Not decided');
    expect(within(row).getByRole('button', { name: 'Always for this series' })).toBeInTheDocument();
    expect(screen.getByText('Lunch').closest('li')).toHaveTextContent('Skipped (your rule)');
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.click(within(row).getByRole('button', { name: 'Send agent' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/meetings/calendar/calls/c1', { action: 'send' }));
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith('m9'));
  });

  it('saves rules', async () => {
    api.get.mockResolvedValue({ data: list([]) });
    api.put.mockResolvedValue({ data: list([]) });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Rules' }));
    fireEvent.change(screen.getByLabelText('Without asking'), { target: { value: 'mine' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save rules' }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/meetings/calendar', expect.objectContaining({ auto_join: 'mine' })));
  });
});
