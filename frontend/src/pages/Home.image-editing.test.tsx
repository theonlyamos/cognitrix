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

vi.mock('@/lib/api', () => ({ api: { get: vi.fn(), post: apiPost } }));
vi.mock('@/context/SessionContext', () => ({ useSession: () => ({ ...harness }) }));
vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path === '/agents' ? [{ id: 'agent-1', name: 'Agent One' }] : [],
    loading: false,
    refetch: vi.fn(),
  }),
}));
vi.mock('@/hooks/useSSE', () => ({
  useSSE: ({ onMessage }: { onMessage: (event: { type: string; artifacts?: ToolArtifact[] }) => void }) => {
    harness.onMessage = onMessage;
    return { isConnected: true, error: null, reconnect: vi.fn() };
  },
}));

function renderHome() {
  return render(<MemoryRouter><Home /></MemoryRouter>);
}

describe('Home image editing transport', () => {
  beforeEach(() => {
    Object.values(harness).forEach((value) => {
      (value as { mockClear?: () => void }).mockClear?.();
    });
    harness.messages = [];
    harness.onMessage = null;
    localStorage.clear();
    localStorage.setItem('selectedAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', '');
    vi.stubGlobal('crypto', { randomUUID: () => 'attachment-1' });
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:local-preview') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    apiPost.mockClear();
    apiPost.mockResolvedValue({ data: {} });
  });

  it('keeps an original File and posts it as multipart without preview data in JSON', async () => {
    const user = userEvent.setup();
    renderHome();
    const file = new File(['original image bytes'], 'source.png', { type: 'image/png' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByText('source.png')).toBeInTheDocument();
    await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'make it brighter');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat', expect.any(FormData)));
    const form = apiPost.mock.calls[0][1] as FormData;
    expect(form.getAll('files')).toEqual([file]);
    const payloadBlob = form.get('payload') as Blob;
    const payload = JSON.parse(await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(reader.error);
      reader.readAsText(payloadBlob);
    }));
    expect(payload).toMatchObject({ message: 'make it brighter', agent_id: 'agent-1' });
    expect(JSON.stringify(payload)).not.toMatch(/dataUrl|blob:|original image bytes/i);
    expect(URL.createObjectURL).toHaveBeenCalledWith(file);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local-preview');
  });

  it('attaches only safe uploaded image refs from attachments_ingested to the latest user message', () => {
    renderHome();
    harness.onMessage?.({ type: 'attachments_ingested', artifacts: [
      { id: 'safe_image_1', mime_type: 'image/png', filename: 'source.png' },
      { id: '../unsafe', mime_type: 'image/png', filename: 'bad.png' },
      { id: 'safe_pdf', mime_type: 'application/pdf', filename: 'skip.pdf' },
    ] });

    expect(harness.attachArtifactsToLatestUser).toHaveBeenCalledWith([
      { id: 'safe_image_1', mime_type: 'image/png', filename: 'source.png', origin: 'uploaded' },
    ]);
  });
});
