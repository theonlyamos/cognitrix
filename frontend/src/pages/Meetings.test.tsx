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
