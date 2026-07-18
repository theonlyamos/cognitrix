import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentType } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage } from '@/context/SessionContext';
import Home from '@/pages/Home';

const homeHarness = vi.hoisted(() => ({
  messages: [] as ChatMessage[],
  onMessage: null as null | ((event: { type: string; content?: string }) => void),
  addMessage: vi.fn(),
  appendToLastMessage: vi.fn(),
  setIsStreaming: vi.fn(),
  addToolCall: vi.fn(),
  resolveToolCall: vi.fn(),
  clearMessages: vi.fn(),
  setMessages: vi.fn(),
}));

const markdownRender = vi.fn();
const scrollIntoViewMock = vi.fn();
const apiGet = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({ api: { get: apiGet } }));

vi.mock('@/components/MarkdownMessage', () => ({
  default: ({ content }: { content: string }) => {
    markdownRender();
    return <div>{content}</div>;
  },
}));

vi.mock('@/context/SessionContext', () => ({
  useSession: () => ({
    messages: homeHarness.messages,
    addMessage: homeHarness.addMessage,
    appendToLastMessage: homeHarness.appendToLastMessage,
    setIsStreaming: homeHarness.setIsStreaming,
    addToolCall: homeHarness.addToolCall,
    resolveToolCall: homeHarness.resolveToolCall,
    clearMessages: homeHarness.clearMessages,
    setMessages: homeHarness.setMessages,
  }),
}));

vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path === '/agents' ? [{ id: 'agent-1', name: 'Agent One' }] : [],
    loading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock('@/hooks/useSSE', () => ({
  useSSE: ({ onMessage }: { onMessage: (event: { type: string; content?: string }) => void }) => {
    homeHarness.onMessage = onMessage;
    return { isConnected: true, error: null, reconnect: vi.fn() };
  },
}));

describe('ChatMessageRow', () => {
  beforeEach(() => {
    markdownRender.mockClear();
    scrollIntoViewMock.mockClear();
    homeHarness.messages = [];
    homeHarness.onMessage = null;
    localStorage.clear();
    localStorage.setItem('selectedAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', '');
    Element.prototype.scrollIntoView = scrollIntoViewMock;
    apiGet.mockReset();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn() });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  });

  it('does not rerender completed markdown when parent state changes', async () => {
    const rowPath = resolve(process.cwd(), 'src/components/ChatMessageRow.tsx');
    expect(existsSync(rowPath), 'ChatMessageRow component should exist').toBe(true);

    const modulePath = '@/components/ChatMessageRow';
    const { ChatMessageRow } = await import(modulePath) as {
      ChatMessageRow: ComponentType<{
        message: ChatMessage;
        isLast: boolean;
        streaming: boolean;
      }>;
    };
    const message: ChatMessage = {
      id: 'assistant-1',
      role: 'assistant',
      content: '**Stable** response',
      timestamp: '2026-07-10T08:30:00.000Z',
    };

    function Parent({ unrelated }: { unrelated: number }) {
      return (
        <>
          <span>parent state: {unrelated}</span>
          <ChatMessageRow message={message} isLast={false} streaming={false} />
        </>
      );
    }

    const { rerender } = render(<Parent unrelated={0} />);
    await waitFor(() => expect(markdownRender).toHaveBeenCalledTimes(1));

    rerender(<Parent unrelated={1} />);

    expect(screen.getByText('parent state: 1')).toBeInTheDocument();
    expect(markdownRender).toHaveBeenCalledTimes(1);
  });

  it('keeps completed historical markdown stable when only the last row starts streaming', async () => {
    const historical: ChatMessage = {
      id: 'assistant-history',
      role: 'assistant',
      content: 'Historical response',
    };
    const active: ChatMessage = {
      id: 'assistant-active',
      role: 'assistant',
      content: 'Active response',
    };
    homeHarness.messages = [historical, active];

    render(
      <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
        <Home />
      </MemoryRouter>,
    );
    await waitFor(() => expect(markdownRender).toHaveBeenCalledTimes(2));

    act(() => homeHarness.onMessage?.({ type: 'generate', content: ' next token' }));

    await waitFor(() => expect(document.querySelector('.caret')).toBeInTheDocument());
    expect(markdownRender).toHaveBeenCalledTimes(2);
  });

  it('keeps long-conversation auto-scroll inside the transcript', async () => {
    homeHarness.messages = [{
      id: 'assistant-1',
      role: 'assistant',
      content: 'first page',
    }];

    const renderHome = () => (
      <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
        <Home />
      </MemoryRouter>
    );
    const { rerender } = render(renderHome());
    const transcript = screen.getByRole('log');

    Object.defineProperty(transcript, 'scrollHeight', {
      configurable: true,
      value: 640,
    });
    Object.defineProperty(transcript, 'scrollTop', {
      configurable: true,
      writable: true,
      value: 0,
    });

    homeHarness.messages = [
      ...homeHarness.messages,
      { id: 'assistant-2', role: 'assistant', content: 'next page' },
    ];
    rerender(renderHome());

    await waitFor(() => expect(transcript.scrollTop).toBe(640));
    expect(scrollIntoViewMock).not.toHaveBeenCalled();
    expect(transcript).toHaveClass(
      'min-h-0',
      'overflow-y-auto',
      'overscroll-contain',
    );
    expect(transcript.parentElement).toHaveClass('min-h-0', 'overflow-hidden');
  });

  it('gives tool summaries a 44px mobile target and compact desktop height', async () => {
    const modulePath = '@/components/ChatMessageRow';
    const { ChatMessageRow } = await import(modulePath) as {
      ChatMessageRow: ComponentType<{
        message: ChatMessage;
        isLast: boolean;
        streaming: boolean;
      }>;
    };
    const toolMessage: ChatMessage = {
      id: 'tool-1',
      role: 'tool',
      content: '',
      tools: [{ id: 'read-1', name: 'read_file', status: 'done' }],
    };

    render(<ChatMessageRow message={toolMessage} isLast streaming={false} />);

    const summary = screen.getByText('read file').closest('summary');
    expect(summary).toHaveClass('min-h-11', 'sm:min-h-0');
  });

  it('renders a stopped tool as a distinct terminal state', async () => {
    const { ChatMessageRow } = await import('@/components/ChatMessageRow');
    const toolMessage: ChatMessage = {
      id: 'tool-stopped',
      role: 'tool',
      content: '',
      tools: [{
        id: 'generate-1',
        name: 'generate_image',
        status: 'stopped',
        result: 'Stopped by user.',
      }],
    };

    render(<ChatMessageRow message={toolMessage} isLast streaming={false} />);

    expect(screen.getByText('Stopped')).toHaveClass('sr-only');
    await userEvent.click(screen.getByText('generate image'));
    expect(screen.getByText('Stopped by user.')).toBeInTheDocument();
  });

  it('shows generated images with separate expand and download controls', async () => {
    apiGet.mockResolvedValue({ data: new Blob(['image'], { type: 'image/png' }) });
    vi.mocked(URL.createObjectURL).mockReturnValue('blob:generated');
    const { ChatMessageRow } = await import('@/components/ChatMessageRow');
    const toolMessage: ChatMessage = {
      id: 'tool-image', role: 'tool', content: '',
      tools: [{
        id: 'generate-1', name: 'generate_image', status: 'done',
        artifacts: [{ id: 'artifact-1', mime_type: 'image/png', filename: 'result.png' }],
      }],
    };
    render(<ChatMessageRow message={toolMessage} isLast streaming={false} />);

    const image = await screen.findByRole('img', { name: 'Generated image' });
    expect(image).toHaveAttribute('src', 'blob:generated');
    expect(image.closest('a')).toBeNull();
    expect(image.closest('button')).toBeNull();
    expect(image.closest('details')).toBeNull();
    expect(screen.getByRole('button', { name: 'Expand generated image' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download result.png' })).toBeInTheDocument();
  });

  it('offers restored generated images as exact edit sources', async () => {
    const user = userEvent.setup();
    const selectSource = vi.fn();
    apiGet.mockResolvedValue({ data: new Blob(['image'], { type: 'image/png' }) });
    vi.mocked(URL.createObjectURL).mockReturnValue('blob:generated');
    const { ChatMessageRow } = await import('@/components/ChatMessageRow');
    const historicalTool: ChatMessage = {
      id: 'tool-history', role: 'tool', content: '',
      tools: [{
        id: 'generate-history', name: 'generate_image', status: 'done',
        artifacts: [{ id: 'artifact-history', mime_type: 'image/png', filename: 'earlier.png' }],
      }],
    };

    render(
      <ChatMessageRow
        message={historicalTool}
        isLast={false}
        streaming={false}
        onEditSource={selectSource}
      />,
    );

    await user.click(await screen.findByRole('button', { name: 'Use earlier.png as edit source' }));
    expect(selectSource).toHaveBeenCalledWith({
      id: 'artifact-history',
      mime_type: 'image/png',
      filename: 'earlier.png',
    });
  });

  it('shows uploaded images with separate Edit, Expand, and Download controls', async () => {
    const selectSource = vi.fn();
    apiGet.mockResolvedValue({ data: new Blob(['image'], { type: 'image/png' }) });
    vi.mocked(URL.createObjectURL).mockReturnValue('blob:uploaded');
    const { ChatMessageRow } = await import('@/components/ChatMessageRow');
    const uploaded: ChatMessage = {
      id: 'user-upload',
      role: 'user',
      content: 'Attached source',
      artifacts: [{ id: 'uploaded-1', mime_type: 'image/png', filename: 'source.png', origin: 'uploaded' }],
    };

    render(
      <ChatMessageRow
        message={uploaded}
        isLast={false}
        streaming={false}
        onEditSource={selectSource}
      />,
    );

    const image = await screen.findByRole('img', { name: 'Attached image' });
    expect(image.closest('button')).toBeNull();
    expect(screen.getByRole('button', { name: 'Use source.png as edit source' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Expand attached image' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download source.png' })).toBeInTheDocument();
  });

  it('opens the generated image in a modal from the expand control', async () => {
    const user = userEvent.setup();
    apiGet.mockResolvedValue({ data: new Blob(['image'], { type: 'image/png' }) });
    vi.mocked(URL.createObjectURL).mockReturnValue('blob:generated');
    const { ChatMessageRow } = await import('@/components/ChatMessageRow');
    const toolMessage: ChatMessage = {
      id: 'tool-image', role: 'tool', content: '',
      tools: [{
        id: 'generate-1', name: 'generate_image', status: 'done',
        artifacts: [{ id: 'artifact-1', mime_type: 'image/png', filename: 'result.png' }],
      }],
    };
    render(<ChatMessageRow message={toolMessage} isLast streaming={false} />);

    const expandButton = await screen.findByRole('button', { name: 'Expand generated image' });
    await user.click(expandButton);

    expect(await screen.findByRole('dialog', { name: 'Generated image preview' })).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('img', { name: 'Generated image, full size' })).toHaveAttribute('src', 'blob:generated');
    expect(screen.getByRole('button', { name: 'Close image preview' })).toHaveFocus();

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog', { name: 'Generated image preview' })).not.toBeInTheDocument();
    expect(expandButton).toHaveFocus();
  });

  it('shows an artifact error with a retry action', async () => {
    apiGet.mockRejectedValue(new Error('nope'));
    const { ChatMessageRow } = await import('@/components/ChatMessageRow');
    const toolMessage: ChatMessage = {
      id: 'tool-error', role: 'tool', content: '',
      tools: [{
        name: 'generate_image', status: 'error',
        artifacts: [{ id: 'artifact-2', mime_type: 'image/png' }],
      }],
    };
    render(<ChatMessageRow message={toolMessage} isLast streaming={false} />);
    expect(await screen.findByText('Generated image unavailable.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry image' })).toBeInTheDocument();
  });

  it('keeps markdown packages behind the Home route boundary', () => {
    const homeSource = readFileSync(
      resolve(process.cwd(), 'src/pages/Home.tsx'),
      'utf8',
    );

    expect(homeSource).not.toMatch(
      /from 'react-markdown'|from 'remark-gfm'|from 'rehype-highlight'/,
    );
  });
});
