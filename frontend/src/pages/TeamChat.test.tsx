import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chatsEdited } from '@/lib/chatNav';
import TeamChat from './TeamChat';

const apiGet = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: vi.fn().mockResolvedValue({ data: {} }), delete: vi.fn().mockResolvedValue({ data: {} }) },
  errorMessage: (_e: unknown, fallback = 'error') => fallback,
}));
vi.mock('@/hooks/useEventStream', () => ({
  useEventStream: () => ({ isConnected: true, isConnecting: false, error: null, reconnect: vi.fn() }),
}));

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.search}</div>;
}

function renderTeamChat(search = '?chat=tc1') {
  return render(
    <MemoryRouter initialEntries={[`/teams/team-1/chat${search}`]} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Routes>
        <Route path="/teams/:teamId/chat" element={<><TeamChat /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('TeamChat and the app sidebar', () => {
  beforeEach(() => {
    Element.prototype.scrollTo = vi.fn();
    apiGet.mockReset().mockImplementation((path: string) => Promise.resolve({
      data: path === '/teams/team-1/chats'
        ? { team: { id: 'team-1', name: 'Launch crew' }, members: [{ id: 'a1', name: 'Writer', handle: 'writer', lead: true }], chats: [{ id: 'tc1', title: 'Plan', message_count: 2 }] }
        : { id: 'tc1', title: 'Plan', message_count: 2, messages: [{ role: 'user', type: 'text', content: 'Draft the launch post' }] },
    }));
  });

  it('leaves the chat on screen when the sidebar deletes it', async () => {
    renderTeamChat();
    expect(await screen.findByText('Draft the launch post')).toBeInTheDocument();
    act(() => chatsEdited({ deletedId: 'tc1' }));
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^$/));
    await waitFor(() => expect(screen.queryByText('Draft the launch post')).not.toBeInTheDocument());
  });

  it('keeps the chat when the sidebar deletes a different one', async () => {
    renderTeamChat();
    expect(await screen.findByText('Draft the launch post')).toBeInTheDocument();
    act(() => chatsEdited({ deletedId: 'other' }));
    expect(screen.getByTestId('where')).toHaveTextContent('?chat=tc1');
  });
});
