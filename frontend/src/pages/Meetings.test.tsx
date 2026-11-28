import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import Meetings, { type MeetingSummary } from './Meetings';

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

  render(
    <MemoryRouter initialEntries={['/meetings?open=m1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Meetings />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Leave call' }));

  expect(await screen.findByText(/^writing the notes…/i)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Leave call' })).not.toBeInTheDocument();
});

it('answers the agent\'s question, steers it privately and pauses notes during a call', async () => {
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
      case '/meetings/m2': return { data: { ...meeting, transcript: '[00:01] Hello', notes: '', guidance: [], live_notes: '**So far**\nPrices discussed.' } };
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

  render(
    <MemoryRouter initialEntries={['/meetings?open=m2']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Meetings />
    </MemoryRouter>,
  );
  expect(await screen.findByText('Can the launch move to Monday?')).toBeInTheDocument();
  expect(screen.getByText('Prices discussed.')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Your answer' }), { target: { value: 'Yes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Answer' }));
  await screen.findByText('Ada passes your answer on.');
  fireEvent.change(screen.getByRole('textbox', { name: 'Private guidance' }), { target: { value: 'No discounts' } });
  fireEvent.click(screen.getByRole('button', { name: 'Pass on' }));
  await screen.findByText('Ada uses it in its answers.');
  fireEvent.click(screen.getByRole('button', { name: 'Pause notes' }));
  await screen.findByText(/^Paused: nothing is kept/);
  expect(posted).toEqual([
    ['/meetings/m2/steer', { kind: 'answer', text: 'Yes' }],
    ['/meetings/m2/steer', { kind: 'note', text: 'No discounts' }],
    ['/meetings/m2/pause', { paused: true }],
  ]);

  fireEvent.change(screen.getByRole('textbox', { name: 'Search meetings' }), { target: { value: 'prices' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  expect(await screen.findByText('…prices go up…')).toBeInTheDocument();
});
