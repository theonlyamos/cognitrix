import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chatsEdited } from '@/lib/chatNav';
import TeamChat from './TeamChat';
import ChatScreen, { TeamChatLink } from './ChatScreen';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost, delete: vi.fn().mockResolvedValue({ data: {} }) },
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
    apiPost.mockReset().mockResolvedValue({ data: {} });
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

  it('sends attached files with the message and lists them on it', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:preview');
    URL.revokeObjectURL = vi.fn();
    apiPost.mockResolvedValue({ data: { session_id: 'tc1', run_id: 'r1' } });
    renderTeamChat();
    await screen.findByText('Draft the launch post');
    const picker = screen.getByLabelText('Attach files', { selector: 'input' });
    const plan = new File(['The code name is Heron.'], 'launch-plan.txt', { type: 'text/plain' });
    const picture = new File([new Uint8Array([137, 80, 78, 71])], 'shapes.png', { type: 'image/png' });
    fireEvent.change(picker, { target: { files: [plan, picture] } });
    expect(await screen.findByText('launch-plan.txt')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove shapes.png' }));
    expect(screen.queryByText('shapes.png')).not.toBeInTheDocument();
    // Files alone are enough to send.
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [path, body] = apiPost.mock.calls[0];
    expect(path).toBe('/teams/team-1/chats');
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).getAll('files').map((f) => (f as File).name)).toEqual(['launch-plan.txt']);
    const sent = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText((body as FormData).get('payload') as Blob);
    });
    expect(JSON.parse(sent)).toEqual({ message: '', session_id: 'tc1' });
    expect(await screen.findByText('📎 launch-plan.txt')).toBeInTheDocument();
  });

  it('refuses a kind of file agents cannot read', async () => {
    renderTeamChat();
    await screen.findByText('Draft the launch post');
    fireEvent.change(screen.getByLabelText('Attach files', { selector: 'input' }), {
      target: { files: [new File(['x'], 'setup.exe', { type: 'application/octet-stream' })] },
    });
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText('setup.exe')).not.toBeInTheDocument();
  });

  it('shows a saved chat with the names the files had when they were sent', async () => {
    apiGet.mockImplementation((path: string) => Promise.resolve({
      data: path === '/teams/team-1/chats'
        ? { team: { id: 'team-1', name: 'Launch crew' }, members: [{ id: 'a1', name: 'Writer', handle: 'writer', lead: true }], chats: [{ id: 'tc1', title: 'Plan', message_count: 3 }] }
        : { id: 'tc1', title: 'Plan', message_count: 3, messages: [
          { role: 'User', type: 'text', content: 'What is the code name?' },
          { role: 'User', type: 'image', content: '[Current image artifact: img_1]', artifact: { id: 'img_1', mime_type: 'image/png' } },
          { role: 'User', type: 'text', content: '[User uploaded files, readable with your file tools:]\nuploads/tc1/f_abc.txt', attachment_names: ['launch-plan.txt'] },
        ] },
    }));
    renderTeamChat();
    expect(await screen.findByText(/What is the code name\?\s+📎 launch-plan\.txt/)).toBeInTheDocument();
    expect(screen.queryByText(/f_abc/)).not.toBeInTheDocument();
    expect(screen.queryByText(/readable with your file tools/)).not.toBeInTheDocument();
  });
});
