import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  agents: [{ id: 'agent-1', name: 'Agent One' }],
  conversations: [] as Array<{ id: string; title: string; message_count: number; updated_at?: string }>,
}));
const apiPost = vi.hoisted(() => vi.fn().mockResolvedValue({ data: {} }));
const apiGet = vi.hoisted(() => vi.fn());
const apiDelete = vi.hoisted(() => vi.fn().mockResolvedValue({ data: {} }));

vi.mock('@/lib/api', () => ({ api: { get: apiGet, post: apiPost, delete: apiDelete } }));
vi.mock('@/context/SessionContext', () => ({ useSession: () => ({ ...harness }) }));
vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path === '/agents'
      ? harness.agents
      : path?.startsWith('/sessions/agents/')
        ? harness.conversations
        : [],
    loading: false,
    refetch: vi.fn(),
  }),
}));
vi.mock('@/hooks/useSSE', () => ({
  useSSE: ({ onMessage }: { onMessage: (event: { type: string; artifacts?: ToolArtifact[] }) => void }) => {
    harness.onMessage = onMessage;
    return { isConnected: true, error: null, reconnect: vi.fn(), streamId: 'stream-1' };
  },
}));

function renderHome() {
  return render(<MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}><Home /></MemoryRouter>);
}

async function readJsonBlob(blob: Blob) {
  return JSON.parse(await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  })) as Record<string, unknown>;
}

function fileInput() {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

function localPreviewRevocations(url: string) {
  return vi.mocked(URL.revokeObjectURL).mock.calls.filter(([revoked]) => revoked === url);
}

async function selectArtifact(artifact: ToolArtifact) {
  harness.messages = [{ id: `message-${artifact.id}`, role: 'user', content: 'source', artifacts: [artifact] }];
  const view = renderHome();
  await userEvent.click(await screen.findByRole('button', {
    name: `Use ${artifact.filename || 'attached-image.png'} as edit source`,
  }));
  return view;
}

describe('Home image editing transport', () => {
  beforeEach(() => {
    Object.values(harness).forEach((value) => {
      if (value && typeof value === 'object' && 'mockClear' in value) {
        (value as { mockClear?: () => void }).mockClear?.();
      }
    });
    harness.messages = [];
    harness.agents = [{ id: 'agent-1', name: 'Agent One' }];
    harness.conversations = [];
    harness.onMessage = null;
    localStorage.clear();
    localStorage.setItem('selectedAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', '');
    let attachmentId = 0;
    vi.stubGlobal('crypto', { randomUUID: () => `attachment-${++attachmentId}` });
    let attachmentPreview = 0;
    let artifactPreview = 0;
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn((value: Blob) => value instanceof File
        ? `blob:local-preview-${++attachmentPreview}`
        : `blob:artifact-preview-${++artifactPreview}`),
    });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    apiGet.mockReset().mockImplementation((path: string) => Promise.resolve({
      data: path.startsWith('/artifacts/') ? new Blob(['preview'], { type: 'image/png' }) : [],
    }));
    apiDelete.mockReset().mockResolvedValue({ data: {} });
    apiPost.mockClear();
    apiPost.mockResolvedValue({ data: {} });
  });

  it('keeps an original File and posts it as multipart without preview data in JSON', async () => {
    const user = userEvent.setup();
    const view = renderHome();
    const file = new File(['original image bytes'], 'source.png', { type: 'image/png' });
    fireEvent.change(fileInput(), { target: { files: [file] } });

    expect(await screen.findByText('source.png')).toBeInTheDocument();
    await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'make it brighter');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat', expect.any(FormData)));
    const form = apiPost.mock.calls[0][1] as FormData;
    expect(form.getAll('files')).toEqual([file]);
    const payload = await readJsonBlob(form.get('payload') as Blob);
    expect(payload).toMatchObject({ message: 'make it brighter', agent_id: 'agent-1', stream_id: 'stream-1', execution_mode: 'chat' });
    expect(JSON.stringify(payload)).not.toMatch(/dataUrl|blob:|original image bytes/i);
    expect(URL.createObjectURL).toHaveBeenCalledWith(file);
    expect(localPreviewRevocations('blob:local-preview-1')).toHaveLength(1);
    view.unmount();
    expect(localPreviewRevocations('blob:local-preview-1')).toHaveLength(1);
  });

  it('keeps a selected Artifact file-free request JSON-only and sends only its id', async () => {
    const artifact = { id: 'artifact-source', mime_type: 'image/png', filename: 'portrait.png', origin: 'generated' as const };
    await selectArtifact(artifact);

    expect(screen.getByText('Editing image')).toBeVisible();
    expect(screen.getByText('portrait.png')).toBeVisible();
    expect(screen.getByTestId('edit-source-thumbnail')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove portrait.png edit source' })).toHaveClass('min-h-11');

    await userEvent.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'make it cinematic');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat', {
      message: 'make it cinematic',
      agent_id: 'agent-1',
      stream_id: 'stream-1',
      edit_source_artifact_id: 'artifact-source',
      execution_mode: 'chat',
    }));
    const body = apiPost.mock.calls[0][1];
    expect(body).not.toBeInstanceOf(FormData);
    expect(JSON.stringify(body)).not.toMatch(/blob:|dataUrl|portrait\.png|mime_type|artifact-source.*artifact-source/i);
    expect(screen.queryByText('Editing image')).not.toBeInTheDocument();
  });

  it('posts repeated original files plus one JSON payload Blob with optional metadata', async () => {
    localStorage.setItem('bypassPermissions', '1');
    localStorage.setItem('chatSession:agent-1', 'conversation-1');
    harness.conversations = [{ id: 'conversation-1', title: 'Current', message_count: 2 }];
    renderHome();
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/sessions/conversation-1/chat'));
    const first = new File(['first'], 'first.png', { type: 'image/png' });
    const second = new File(['second'], 'notes.txt', { type: 'text/plain' });
    fireEvent.change(fileInput(), { target: { files: [first, second] } });
    await userEvent.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'use both');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    const form = await waitFor(() => apiPost.mock.calls.find(([path]) => path === '/agents/chat')?.[1] as FormData);
    expect(form.getAll('files')).toEqual([first, second]);
    expect(form.getAll('payload')).toHaveLength(1);
    expect(form.get('payload')).toBeInstanceOf(Blob);
    expect(await readJsonBlob(form.get('payload') as Blob)).toEqual({
      message: 'use both',
      agent_id: 'agent-1',
      stream_id: 'stream-1',
      session_id: 'conversation-1',
      bypass_permissions: true,
      execution_mode: 'chat',
    });
  });

  it.each([
    ['oversized', [new File([new Uint8Array((10 * 1024 * 1024) + 1)], 'huge.png', { type: 'image/png' })], /larger than 10 MB/],
    ['count', Array.from({ length: 21 }, (_, index) => new File(['x'], `${index}.png`, { type: 'image/png' })), /20 files/],
    ['total', [
      new File([new Uint8Array(9 * 1024 * 1024)], 'one.png', { type: 'image/png' }),
      new File([new Uint8Array(9 * 1024 * 1024)], 'two.png', { type: 'image/png' }),
      new File([new Uint8Array(9 * 1024 * 1024)], 'three.png', { type: 'image/png' }),
    ], /25 MB total/],
  ])('blocks %s validation failures before the chat API call', async (_name, files, error) => {
    renderHome();
    fireEvent.change(fileInput(), { target: { files } });
    expect(await screen.findByRole('alert')).toHaveTextContent(error);
    await userEvent.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'send anyway');
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(apiPost).not.toHaveBeenCalledWith('/agents/chat', expect.anything());
  });

  it('replaces the selected Artifact exactly and clears it accessibly', async () => {
    harness.messages = [{
      id: 'sources', role: 'user', content: 'sources', artifacts: [
        { id: 'first-artifact', mime_type: 'image/png', filename: 'first.png', origin: 'uploaded' },
        { id: 'second-artifact', mime_type: 'image/png', filename: 'second.png', origin: 'generated' },
      ],
    }];
    renderHome();
    await userEvent.click(await screen.findByRole('button', { name: 'Use first.png as edit source' }));
    expect(screen.getByText('first.png')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Use second.png as edit source' }));
    expect(screen.queryByText('first.png')).not.toBeInTheDocument();
    expect(screen.getByText('second.png')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Remove second.png edit source' }));
    expect(screen.queryByText('Editing image')).not.toBeInTheDocument();
  });

  it('revokes removed and replacement attachment previews exactly once', async () => {
    const view = renderHome();
    const original = new File(['one'], 'original.png', { type: 'image/png' });
    fireEvent.change(fileInput(), { target: { files: [original] } });
    await userEvent.click(await screen.findByRole('button', { name: 'Remove original.png' }));
    expect(localPreviewRevocations('blob:local-preview-1')).toHaveLength(1);

    const replacement = new File(['two'], 'replacement.png', { type: 'image/png' });
    fireEvent.change(fileInput(), { target: { files: [replacement] } });
    expect(await screen.findByRole('button', { name: 'Remove replacement.png' })).toBeInTheDocument();
    expect(localPreviewRevocations('blob:local-preview-2')).toHaveLength(0);

    view.unmount();
    expect(localPreviewRevocations('blob:local-preview-1')).toHaveLength(1);
    expect(localPreviewRevocations('blob:local-preview-2')).toHaveLength(1);
  });

  it('restores live previews after failed send and later revokes them once on removal and unmount', async () => {
    apiPost.mockRejectedValueOnce(new Error('offline'));
    const view = renderHome();
    const retry = new File(['one'], 'retry.png', { type: 'image/png' });
    const retained = new File(['two'], 'retained.png', { type: 'image/png' });
    fireEvent.change(fileInput(), { target: { files: [retry, retained] } });
    await userEvent.click(await screen.findByRole('button', { name: 'Use retry.png as edit source' }));
    await userEvent.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'retry edit');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByRole('button', { name: 'Remove retry.png' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Use retry.png as edit source' })).toHaveAttribute('aria-pressed', 'true');
    expect(document.querySelector('img[alt=""]')).toHaveAttribute('src', 'blob:local-preview-1');
    expect(localPreviewRevocations('blob:local-preview-1')).toHaveLength(0);
    expect(localPreviewRevocations('blob:local-preview-2')).toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: 'Remove retry.png' }));
    expect(localPreviewRevocations('blob:local-preview-1')).toHaveLength(1);
    expect(localPreviewRevocations('blob:local-preview-2')).toHaveLength(0);
    view.unmount();
    expect(localPreviewRevocations('blob:local-preview-1')).toHaveLength(1);
    expect(localPreviewRevocations('blob:local-preview-2')).toHaveLength(1);
  });

  it.each([
    ['conversation switch', async () => {
      await userEvent.click(screen.getByRole('button', { name: 'Second' }));
    }],
    ['agent switch', async () => {
      await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Active agent' }), 'agent-2');
    }],
    ['new conversation', async () => {
      await userEvent.click(screen.getByRole('button', { name: '+ new' }));
    }],
    ['active conversation deletion', async () => {
      await userEvent.click(screen.getByRole('button', { name: 'Delete conversation Current' }));
    }],
  ])('revokes a pending preview exactly once on %s', async (_scenario, runLifecycle) => {
    vi.stubGlobal('confirm', vi.fn(() => true));
    harness.agents = [{ id: 'agent-1', name: 'Agent One' }, { id: 'agent-2', name: 'Agent Two' }];
    harness.conversations = [
      { id: 'conversation-1', title: 'Current', message_count: 1 },
      { id: 'conversation-2', title: 'Second', message_count: 1 },
    ];
    localStorage.setItem('chatSession:agent-1', 'conversation-1');
    const view = renderHome();
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/sessions/conversation-1/chat'));

    const file = new File(['preview'], 'pending.png', { type: 'image/png' });
    fireEvent.change(fileInput(), { target: { files: [file] } });
    expect(await screen.findByRole('button', { name: 'Remove pending.png' })).toBeInTheDocument();
    await runLifecycle();

    await waitFor(() => expect(localPreviewRevocations('blob:local-preview-1')).toHaveLength(1));
    view.unmount();
    expect(localPreviewRevocations('blob:local-preview-1')).toHaveLength(1);
  });

  it('clears selected Artifact on conversation and agent switches and on new conversation', async () => {
    harness.agents = [{ id: 'agent-1', name: 'Agent One' }, { id: 'agent-2', name: 'Agent Two' }];
    harness.conversations = [
      { id: 'conversation-1', title: 'First', message_count: 1 },
      { id: 'conversation-2', title: 'Second', message_count: 1 },
    ];
    localStorage.setItem('chatSession:agent-1', 'conversation-1');
    harness.messages = [{ id: 'source', role: 'user', content: '', artifacts: [
      { id: 'artifact-source', mime_type: 'image/png', filename: 'source.png', origin: 'uploaded' },
    ] }];
    renderHome();
    await userEvent.click(await screen.findByRole('button', { name: 'Use source.png as edit source' }));
    await userEvent.click(screen.getByRole('button', { name: 'Second' }));
    await waitFor(() => expect(screen.queryByText('Editing image')).not.toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Use source.png as edit source' }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Active agent' }), 'agent-2');
    expect(screen.queryByText('Editing image')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Use source.png as edit source' }));
    await userEvent.click(screen.getByRole('button', { name: '+ new' }));
    expect(screen.queryByText('Editing image')).not.toBeInTheDocument();
  });

  it('clears selected Artifact when the active conversation is deleted', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true));
    harness.conversations = [{ id: 'conversation-1', title: 'Current', message_count: 1 }];
    localStorage.setItem('chatSession:agent-1', 'conversation-1');
    harness.messages = [{ id: 'source', role: 'user', content: '', artifacts: [
      { id: 'artifact-source', mime_type: 'image/png', filename: 'source.png', origin: 'uploaded' },
    ] }];
    renderHome();
    await userEvent.click(await screen.findByRole('button', { name: 'Use source.png as edit source' }));
    await userEvent.click(screen.getByRole('button', { name: 'Delete conversation Current' }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/sessions/conversation-1'));
    expect(screen.queryByText('Editing image')).not.toBeInTheDocument();
  });

  it('attaches only safe uploaded image refs and ignores malformed mime types', () => {
    renderHome();
    expect(() => act(() => harness.onMessage?.({ type: 'attachments_ingested', artifacts: [
      { id: 'safe_image_1', mime_type: 'image/png', filename: 'source.png' },
      { id: '../unsafe', mime_type: 'image/png', filename: 'bad.png' },
      { id: 'safe_pdf', mime_type: 'application/pdf', filename: 'skip.pdf' },
      { id: 'missing-mime' } as ToolArtifact,
      { id: 'number-mime', mime_type: 42 } as unknown as ToolArtifact,
    ] }))).not.toThrow();

    expect(harness.attachArtifactsToLatestUser).toHaveBeenCalledWith([
      { id: 'safe_image_1', mime_type: 'image/png', filename: 'source.png', origin: 'uploaded' },
    ]);
  });

  it.each(['uploaded', 'generated'] as const)('restores %s image refs into an editable next request', async (origin) => {
    harness.conversations = [{ id: 'conversation-1', title: 'Current', message_count: 1 }];
    localStorage.setItem('chatSession:agent-1', 'conversation-1');
    apiGet.mockImplementation((path: string) => Promise.resolve({
      data: path === '/sessions/conversation-1/chat' ? [
        { role: 'user', type: 'text', content: 'Use this source' },
        { role: 'user', type: 'image', artifact: { id: `restored-${origin}`, mime_type: 'image/png', origin, filename: 'restored.png', width: 16, height: 9 } },
      ] : new Blob(['preview'], { type: 'image/png' }),
    }));
    const view = renderHome();
    await waitFor(() => expect(harness.setMessages).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ artifacts: [expect.objectContaining({ id: `restored-${origin}` })] }),
    ])));
    harness.messages = harness.setMessages.mock.calls[harness.setMessages.mock.calls.length - 1]?.[0] as ChatMessage[];
    view.rerender(<MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}><Home /></MemoryRouter>);

    await userEvent.click(await screen.findByRole('button', { name: 'Use restored.png as edit source' }));
    await userEvent.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'make it brighter');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat', expect.objectContaining({
      message: 'make it brighter', edit_source_artifact_id: `restored-${origin}`,
    })));
  });
});
