import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage, ToolArtifact } from '@/context/SessionContext';
import Home from './Home';

const harness = vi.hoisted(() => ({
  messages: [] as ChatMessage[],
  onMessage: null as null | ((event: { type: string; artifacts?: ToolArtifact[] }) => void),
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
const apiPost = vi.hoisted(() => vi.fn().mockResolvedValue({ data: {} }));
const apiGet = vi.hoisted(() => vi.fn().mockResolvedValue({ data: [] }));

vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost, delete: vi.fn().mockResolvedValue({ data: {} }) },
}));
vi.mock('@/context/SessionContext', () => ({ useSession: () => ({ ...harness }) }));
vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path === '/agents' ? [{ id: 'agent-1', name: 'Agent One' }] : [],
    loading: false,
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
    <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Home />
    </MemoryRouter>,
  );
}

describe('Home explicit execution mode', () => {
  beforeEach(() => {
    apiPost.mockReset().mockResolvedValue({ data: {} });
    apiGet.mockReset().mockResolvedValue({ data: [] });
    localStorage.clear();
    localStorage.setItem('selectedAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', '');
  });

  it('uses chat mode for Enter', async () => {
    const user = userEvent.setup();
    renderHome();
    await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'hello{enter}');

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat', expect.objectContaining({
      message: 'hello',
      execution_mode: 'chat',
    })));
  });

  it('uses task mode only for the Run as task action', async () => {
    const user = userEvent.setup();
    renderHome();
    await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'prepare a report');
    await user.click(screen.getByRole('button', { name: 'Run as task' }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat', expect.objectContaining({
      message: 'prepare a report',
      execution_mode: 'task',
    })));
  });

  it('disables task mode when an attachment is selected', async () => {
    renderHome();
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [new File(['x'], 'note.txt', { type: 'text/plain' })] },
    });

    expect(await screen.findByRole('button', { name: 'Run as task' })).toBeDisabled();
  });
});
