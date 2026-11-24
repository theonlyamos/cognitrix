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
  markTurnDuration: vi.fn(),
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

vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost, patch: vi.fn(), delete: vi.fn().mockResolvedValue({ data: {} }) },
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

function renderChill(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Home chill />
    </MemoryRouter>,
  );
}

describe('Home in chill mode: which chat opens', () => {
  beforeEach(() => {
    apiPost.mockReset().mockResolvedValue({ data: {} });
    apiGet.mockReset().mockImplementation((path: string) => Promise.resolve({
      data: path === '/assistant/main' ? { main_assistant: { id: 'agent-1' } }
        : path === '/sessions/c1/chat' ? [{ role: 'user', type: 'text', content: 'Hi' }] : [],
    }));
    harness.messages = [];
    localStorage.clear();
    localStorage.setItem('chillAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', 'c1');
  });

  it('brings the last chat along when switched to from a chat', async () => {
    renderChill('/chill');
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/sessions/c1/chat'));
  });

  it('starts on a new, empty chat when switched to from another page', async () => {
    renderChill('/chill?new=1');
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/assistant/main'));
    // Give the restore a chance to run: it must not open the last chat.
    await new Promise((r) => setTimeout(r, 300));
    expect(apiGet).not.toHaveBeenCalledWith('/sessions/c1/chat');
    expect(apiGet).not.toHaveBeenCalledWith('/sessions/c2/chat');
  });
});
