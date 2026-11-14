import { render, waitFor } from '@testing-library/react';
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
  { id: 'c1', title: 'Plain chat', message_count: 2, kind: 'agent', agent_id: 'agent-1', team_id: null, updated_at: '2026-11-01T10:00:00Z' },
  {
    id: 'c2', title: 'Find the release date', message_count: 2, kind: 'agent', agent_id: 'agent-1', team_id: null,
    updated_at: '2026-11-02T10:00:00Z', started_by: { agent_id: 'agent-2', agent_name: 'Boss', session_id: 'c0' },
  },
]);
const apiPost = vi.hoisted(() => vi.fn().mockResolvedValue({ data: {} }));
const apiGet = vi.hoisted(() => vi.fn().mockResolvedValue({ data: [] }));

vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost, delete: vi.fn().mockResolvedValue({ data: {} }) },
}));
vi.mock('@/context/SessionContext', () => ({ useSession: () => ({ ...harness }) }));
vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path === '/agents'
      ? [{ id: 'agent-1', name: 'Agent One' }, { id: 'agent-2', name: 'Boss' }]
      : path === '/sessions/chats' ? CHATS : [],
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

function renderHome(entry = '/home') {
  return render(
    <MemoryRouter initialEntries={[entry]} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Home />
    </MemoryRouter>,
  );
}

describe('Home chats started by agents', () => {
  beforeEach(() => {
    apiGet.mockReset().mockResolvedValue({ data: [] });
    localStorage.clear();
    localStorage.setItem('selectedAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', '');
  });

  it('opens the chat named in the link', async () => {
    renderHome('/home?chat=c2');
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/sessions/c2/chat'));
  });
});
