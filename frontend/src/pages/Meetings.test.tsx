import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import Meetings, { type MeetingSummary } from './Meetings';

afterEach(() => vi.restoreAllMocks());

const renderPage = (path: string) => render(
  <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
    <Meetings />
  </MemoryRouter>,
);

it('shows notes progress after leaving a call while its worker is still running', async () => {
  let meeting: MeetingSummary = {
    id: 'm1', title: 'Design review', filename: '', agent_id: 'a1', status: 'live',
    progress: 'listening', seconds: 60, error: '', created_at: '2026-11-28T10:00:00Z',
    has_transcript: true, kind: 'live', platform: 'meet', bot_name: 'Ada', in_call: true,
  };
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    switch (path) {
      case '/agents': return { data: [{ id: 'a1', name: 'Ada' }] };
      case '/meetings': return { data: { meetings: [meeting], voice_ready: true, ffmpeg: true } };
      case '/meetings/calendar': return { data: { connected: false, calls: [] } };
      case '/meetings/m1': return { data: { ...meeting, transcript: '[00:01] Hello', notes: '' } };
      default: throw new Error(`Unexpected GET ${path}`);
    }
  });
  vi.spyOn(api, 'post').mockImplementation(async (path) => {
    if (path !== '/meetings/m1/leave') throw new Error(`Unexpected POST ${path}`);
    meeting = { ...meeting, status: 'writing', progress: 'writing the notes' };
    return { data: meeting };
  });
  HTMLElement.prototype.scrollIntoView = vi.fn();

  renderPage('/meetings?open=m1');
  fireEvent.click(await screen.findByRole('button', { name: 'Leave' }));

  expect(await screen.findByText(/^writing the notes…/i)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Leave' })).not.toBeInTheDocument();
});

it('answers the agent\'s question, steers it privately, talks in the call and pauses notes', async () => {
  const meeting: MeetingSummary = {
    id: 'm2', title: 'Pricing call', filename: '', agent_id: 'a1', status: 'live',
    progress: 'listening', seconds: 60, error: '', created_at: '2026-11-28T10:00:00Z',
    has_transcript: true, kind: 'live', platform: 'meet', bot_name: 'Ada', in_call: true,
    owner_question: 'Can the launch move to Monday?', paused: false,
  };
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    switch (path) {
      case '/agents': return { data: [{ id: 'a1', name: 'Ada' }] };
      case '/meetings': return { data: { meetings: [meeting], voice_ready: true, ffmpeg: true } };
      case '/meetings/calendar': return { data: { connected: false, calls: [] } };
      case '/meetings/m2': return { data: { ...meeting, transcript: '[00:01] Hello, the budget is tight', notes: '', guidance: [], watch_words: ['budget'], live_notes: '**So far**\nPrices discussed.' } };
      case '/meetings/search': return { data: { results: [{ id: 'm2', title: 'Pricing call', date: '2026-11-28', kind: 'live', snippet: '…prices go up…' }] } };
      default: throw new Error(`Unexpected GET ${path}`);
    }
  });
  const posted: [string, unknown][] = [];
  vi.spyOn(api, 'post').mockImplementation(async (path, body) => {
    posted.push([path, body]);
    return { data: { status: 'queued' } };
  });
  HTMLElement.prototype.scrollIntoView = vi.fn();

  renderPage('/meetings?open=m2');
  expect(await screen.findByText('Can the launch move to Monday?')).toBeInTheDocument();
  expect(screen.getAllByText('Prices discussed.').length).toBeGreaterThan(0);
  expect(screen.getByText('budget', { selector: 'mark' })).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Your answer' }), { target: { value: 'Yes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Answer' }));
  await screen.findByText('Ada passes your answer on.');

  fireEvent.change(screen.getByRole('textbox', { name: 'Say in the call' }), { target: { value: 'Thanks all' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await screen.findByText('Sent to the call.');

  fireEvent.click(screen.getByRole('radio', { name: 'Tell Ada' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Private guidance' }), { target: { value: 'No discounts' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await screen.findByText('Ada uses it in its answers.');

  fireEvent.click(screen.getByRole('radio', { name: 'Bring it up' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Private guidance' }), { target: { value: 'Ask about legal' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await screen.findByText('Ada brings it up at the next pause.');

  fireEvent.click(screen.getByRole('button', { name: 'Pause notes' }));
  await screen.findByText(/^Paused: nothing is kept/);
  expect(posted).toEqual([
    ['/meetings/m2/steer', { kind: 'answer', text: 'Yes' }],
    ['/meetings/m2/say', { text: 'Thanks all' }],
    ['/meetings/m2/steer', { kind: 'note', text: 'No discounts' }],
    ['/meetings/m2/steer', { kind: 'raise', text: 'Ask about legal' }],
    ['/meetings/m2/pause', { paused: true }],
  ]);

  const [search] = screen.getAllByRole('searchbox', { name: 'Search meetings' });
  fireEvent.change(search, { target: { value: 'prices' } });
  fireEvent.submit(search.closest('form')!);
  expect(await screen.findByText('…prices go up…')).toBeInTheDocument();
});

it('groups the list, opens a finished meeting with its action items, and sends an agent from the dialog', async () => {
  const today = new Date().toISOString();
  const done: MeetingSummary = {
    id: 'm3', title: 'Design review', filename: '', agent_id: 'a1', status: 'done',
    progress: '', seconds: 1800, error: '', created_at: today, has_transcript: true, kind: 'live', platform: 'meet', bot_name: 'Ada',
  };
  const upload: MeetingSummary = {
    id: 'm4', title: 'Q4 planning', filename: 'q4.mp3', agent_id: 'a1', status: 'done',
    progress: '', seconds: 2880, error: '', created_at: '2026-01-02T10:00:00Z', has_transcript: true, kind: 'recording',
  };
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    switch (path) {
      case '/agents': return { data: [{ id: 'a1', name: 'Ada' }] };
      case '/meetings': return { data: { meetings: [done, upload], voice_ready: true, ffmpeg: true, live: { computer: true, speech: false } } };
      case '/meetings/calendar': return { data: { connected: false, calls: [] } };
      case '/meetings/m3': return { data: { ...done, transcript: '[00:01] Hi', notes: '## Summary\nScreen two wins.', action_items: [{ owner: 'Kofi', what: 'set up the flag' }] } };
      case '/meetings/m9': return { data: { ...done, id: 'm9', status: 'joining', transcript: '', notes: '' } };
      default: throw new Error(`Unexpected GET ${path}`);
    }
  });
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { id: 'm9' } });
  HTMLElement.prototype.scrollIntoView = vi.fn();

  renderPage('/meetings');
  const list = await screen.findByRole('navigation', { name: 'Meetings' });
  expect(within(list).getByText('Today')).toBeInTheDocument();
  expect(within(list).getByText('Earlier')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: /Recordings/ }));
  expect(within(list).queryByText('Design review')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'All' }));

  fireEvent.click(within(list).getByText('Design review'));
  expect(await screen.findByText('Screen two wins.')).toBeInTheDocument();
  expect(screen.getByRole('complementary', { name: 'Action items' })).toHaveTextContent('Kofi set up the flag');

  fireEvent.click(screen.getAllByRole('button', { name: /Send agent to a call/ })[0]);
  const dialog = await screen.findByRole('dialog', { name: 'Send an agent to a call' });
  fireEvent.change(within(dialog).getByPlaceholderText('https://meet.google.com/abc-defg-hij'), { target: { value: 'https://zoom.us/j/123' } });
  expect(within(dialog).getByText('Zoom')).toBeInTheDocument();
  expect(within(dialog).getByRole('radio', { name: /Answer out loud/ })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Join the call' }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/meetings/live', expect.objectContaining({ url: 'https://zoom.us/j/123', reply_mode: 'chat' })));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('retries a failed call with the same announcement choice, and offers a way back when a meeting will not load', async () => {
  const failed: MeetingSummary = {
    id: 'm5', title: 'Standup', filename: '', agent_id: 'a1', status: 'error',
    progress: '', seconds: 0, error: 'Nobody let Ada in.', created_at: '2026-11-28T10:00:00Z', has_transcript: false,
    kind: 'live', platform: 'meet', bot_name: 'Ada', url: 'https://meet.google.com/abc-defg-hij', reply_mode: 'chat', announce: false,
  };
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    switch (path) {
      case '/agents': return { data: [{ id: 'a1', name: 'Ada' }] };
      case '/meetings': return { data: { meetings: [failed], voice_ready: true, ffmpeg: true } };
      case '/meetings/calendar': return { data: { connected: false, calls: [] } };
      case '/meetings/m5': return { data: { ...failed, transcript: '', notes: '' } };
      case '/meetings/gone': throw new Error('Not found');
      default: throw new Error(`Unexpected GET ${path}`);
    }
  });
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { id: 'gone' } });
  HTMLElement.prototype.scrollIntoView = vi.fn();

  renderPage('/meetings?open=m5');
  fireEvent.click(await screen.findByRole('button', { name: /Try again/ }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/meetings/live', expect.objectContaining({ announce: false })));

  fireEvent.click(await screen.findByRole('button', { name: 'Back to meetings' }));
  expect(await screen.findByText('Pick a meeting to see its notes')).toBeInTheDocument();
});
