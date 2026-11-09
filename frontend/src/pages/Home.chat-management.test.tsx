import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage, ToolArtifact } from '@/context/SessionContext';
import Home from './Home';

const harness = vi.hoisted(() => ({
  messages: [] as ChatMessage[],
  onMessage: null as null | ((event: Record<string, unknown> & { type: string; artifacts?: ToolArtifact[] }) => void),
  addMessage: vi.fn(),
  appendToLastMessage: vi.fn(),
  attachArtifactsToLatestUser: vi.fn(),
  setIsStreaming: vi.fn(),
  addToolCall: vi.fn(),
  resolveToolCall: vi.fn(),
  stopRunningTools: vi.fn(),
  failRunningTools: vi.fn(),
  clearMessages: vi.fn(),
  setMessages: vi.fn(),
}));
const CHATS = vi.hoisted(() => [
  { id: 'c1', title: 'Trip to Accra', message_count: 4, kind: 'agent', agent_id: 'agent-1', team_id: null, updated_at: '2026-11-03T10:00:00Z' },
  { id: 'c2', title: 'Budget review', message_count: 2, kind: 'agent', agent_id: 'agent-1', team_id: null, updated_at: '2026-11-01T10:00:00Z', pinned: true },
  { id: 'c3', title: 'Old notes', message_count: 2, kind: 'agent', agent_id: 'agent-1', team_id: null, updated_at: '2026-10-01T10:00:00Z', archived: true },
]);
const apiPost = vi.hoisted(() => vi.fn());
const apiGet = vi.hoisted(() => vi.fn());
const apiPatch = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost, patch: apiPatch, delete: vi.fn().mockResolvedValue({ data: {} }) },
}));
vi.mock('@/context/SessionContext', () => ({ useSession: () => ({ ...harness }) }));
vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path === '/agents' ? [{ id: 'agent-1', name: 'Agent One' }]
      : path === '/sessions/chats' || path?.startsWith('/sessions/agents/') ? CHATS : [],
    loading: false,
    loadedPath: path,
    refetch: vi.fn(),
  }),
}));
vi.mock('@/hooks/useSSE', () => ({
  useSSE: ({ onMessage }: { onMessage: (event: { type: string }) => void }) => {
    harness.onMessage = onMessage;
    return { isConnected: true, error: null, reconnect: vi.fn(), streamId: 'stream-1' };
  },
}));

function renderHome() {
  return render(
    <MemoryRouter initialEntries={['/home']} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Home />
    </MemoryRouter>,
  );
}

const chatTitles = () =>
  screen.getAllByRole('button', { pressed: false })
    .map((button) => button.textContent || '')
    .filter((text) => /Accra|Budget|Old notes/.test(text));

describe('Home chat list management', () => {
  beforeEach(() => {
    apiGet.mockReset().mockResolvedValue({ data: [] });
    apiPost.mockReset().mockResolvedValue({ data: {} });
    apiPatch.mockReset().mockResolvedValue({ data: {} });
    harness.messages = [];
    harness.setMessages.mockReset();
    localStorage.clear();
    localStorage.setItem('selectedAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', '');
  });

  it('lists pinned chats first and keeps archived chats out of the way', async () => {
    renderHome();
    await screen.findAllByText('Budget review');
    const titles = chatTitles();
    expect(titles[0]).toContain('Budget review');
    expect(titles[1]).toContain('Trip to Accra');
    expect(screen.queryByText('Old notes')).not.toBeInTheDocument();

    await userEvent.click(screen.getAllByRole('button', { name: 'archived 1' })[0]);
    expect(screen.getAllByText('Old notes').length).toBeGreaterThan(0);
    expect(screen.queryByText('Trip to Accra')).not.toBeInTheDocument();
  });

  it('renames, pins and archives from the row menu', async () => {
    renderHome();
    await userEvent.click((await screen.findAllByRole('button', { name: 'More actions for Trip to Accra' }))[0]);
    await userEvent.click(screen.getByRole('menuitem', { name: 'Pin to top' }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith('/sessions/c1', { pinned: true }));

    await userEvent.click(screen.getAllByRole('button', { name: 'More actions for Trip to Accra' })[0]);
    await userEvent.click(screen.getByRole('menuitem', { name: 'Rename' }));
    const box = screen.getByRole('textbox', { name: 'Chat name' });
    await userEvent.clear(box);
    await userEvent.type(box, 'Ghana holiday{Enter}');
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith('/sessions/c1', { title: 'Ghana holiday' }));

    await userEvent.click(screen.getAllByRole('button', { name: 'More actions for Budget review' })[0]);
    expect(screen.getByRole('menuitem', { name: 'Unpin' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Archive' }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith('/sessions/c2', { archived: true }));
  });

  it('filters by title as you type and shows chats found by their messages', async () => {
    apiGet.mockImplementation((path: string) => Promise.resolve({
      data: path === '/sessions/search'
        ? [{ id: 'c2', title: 'Budget review', agent_id: 'agent-1', snippet: 'flights to Accra cost 900' }]
        : [],
    }));
    renderHome();
    await userEvent.type((await screen.findAllByRole('searchbox', { name: 'Search chats' }))[0], 'accra');
    expect(screen.queryByText('Budget review')).not.toBeInTheDocument();
    expect((await screen.findAllByText('flights to Accra cost 900')).length).toBeGreaterThan(0);
    expect(apiGet).toHaveBeenCalledWith('/sessions/search', { params: { q: 'accra' } });
  });
});

describe('Home edit and regenerate', () => {
  const history = [
    { role: 'user', type: 'text', content: 'First question' },
    { role: 'assistant', type: 'text', content: 'First answer' },
    { role: 'user', type: 'text', content: 'Second question' },
    { role: 'assistant', type: 'text', content: 'Second answer' },
  ];

  beforeEach(() => {
    apiPost.mockReset().mockResolvedValue({ data: {} });
    apiGet.mockReset().mockImplementation((path: string) =>
      Promise.resolve({ data: path === '/sessions/c1/chat' ? history : [] }));
    harness.setMessages.mockReset();
    harness.addMessage.mockReset();
    harness.messages = [
      { id: 'm0', role: 'user', content: 'First question' },
      { id: 'm1', role: 'assistant', content: 'First answer' },
      { id: 'm2', role: 'user', content: 'Second question' },
      { id: 'm3', role: 'assistant', content: 'Second answer' },
    ];
    localStorage.clear();
    localStorage.setItem('selectedAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', 'c1');
  });

  it('regenerates the last reply by rewinding to the last question', async () => {
    renderHome();
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/sessions/c1/chat'));
    const regenerate = await screen.findAllByRole('button', { name: 'regenerate' });
    expect(regenerate).toHaveLength(1);
    await userEvent.click(regenerate[0]);
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(
      '/sessions/c1/rewind', { turn: 1, expected: 'Second question' },
    ));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(
      '/agents/chat', expect.objectContaining({ message: 'Second question', session_id: 'c1' }),
    ));
    expect(harness.setMessages).toHaveBeenCalledWith(harness.messages.slice(0, 2));
  });

  it('puts an earlier message back in the box and replaces it on send', async () => {
    renderHome();
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/sessions/c1/chat'));
    const first = (await screen.findByText('First question')).closest('.group\\/msg') as HTMLElement;
    await userEvent.click(within(first).getByRole('button', { name: 'edit' }));
    const box = screen.getByLabelText('Message the agent');
    expect(box).toHaveValue('First question');
    expect(screen.getByText(/Editing an earlier message/)).toBeInTheDocument();
    await userEvent.clear(box);
    await userEvent.type(box, 'A better question');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(
      '/sessions/c1/rewind', { turn: 0, expected: 'First question' },
    ));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(
      '/agents/chat', expect.objectContaining({ message: 'A better question' }),
    ));
  });
});
