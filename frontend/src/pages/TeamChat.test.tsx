import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chatsEdited } from '@/lib/chatNav';
import TeamChat from './TeamChat';
import ChatScreen, { TeamChatLink } from './ChatScreen';

const apiGet = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: vi.fn().mockResolvedValue({ data: {} }), delete: vi.fn().mockResolvedValue({ data: {} }) },
  errorMessage: (_e: unknown, fallback = 'error') => fallback,
}));
vi.mock('@/pages/Home', () => ({ default: () => <div>one-agent chat</div> }));
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

  it('opens on the main chat screen and keeps the team in the address', async () => {
    render(
      <MemoryRouter initialEntries={['/teams/team-1/chat?chat=tc1']} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
        <Routes>
          <Route path="/teams/:teamId/chat" element={<TeamChatLink />} />
          <Route path="/home" element={<><ChatScreen /><Where /></>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText('Draft the launch post')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('?team=team-1&chat=tc1');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Plan');
    // "New chat" leaves the chat but stays with the team.
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\?team=team-1$/));
    expect(await screen.findByRole('heading', { name: 'Talk to the whole team' })).toBeInTheDocument();
    expect(screen.queryByText('one-agent chat')).not.toBeInTheDocument();
  });

  it('shows the one-agent chat when no team is in the address', async () => {
    render(
      <MemoryRouter initialEntries={['/home']} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
        <Routes><Route path="/home" element={<ChatScreen />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText('one-agent chat')).toBeInTheDocument();
  });

  it('offers who should answer from the Mention button', async () => {
    renderTeamChat('');
    fireEvent.click(await screen.findByRole('button', { name: /Mention$/ }));
    expect(await screen.findByRole('listbox', { name: 'Mention a teammate' })).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('option', { name: /Writer/ }));
    expect(screen.getByRole('combobox', { name: 'Message the team' })).toHaveValue('@writer ');
  });
});
