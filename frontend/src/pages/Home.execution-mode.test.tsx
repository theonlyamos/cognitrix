import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  markTurnDuration: vi.fn(),
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

  it('quotes text picked in a reply into the next message', async () => {
    harness.messages = [
      { id: 'u1', role: 'user', content: 'Which port?' },
      { id: 'a1', role: 'assistant', content: 'Use port 8080 for the server.' },
    ] as ChatMessage[];
    try {
      const user = userEvent.setup();
      renderHome();
      // Wait for the Markdown view, so the picked text stays on the page.
      await waitFor(() => expect(document.querySelector('[data-reply-source] p')).not.toBeNull());
      const reply = document.querySelector('[data-reply-source] p')!;
      const range = document.createRange();
      range.selectNodeContents(reply);
      window.getSelection()!.removeAllRanges();
      window.getSelection()!.addRange(range);
      fireEvent.pointerUp(document);
      fireEvent.click(await screen.findByRole('button', { name: 'Reply' }));
      expect(await screen.findByRole('button', { name: 'Remove quote' })).toBeInTheDocument();
      await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'why this one?{enter}');

      await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat', expect.objectContaining({
        message: '> Use port 8080 for the server.\n\nwhy this one?',
      })));
      expect(screen.queryByRole('button', { name: 'Remove quote' })).not.toBeInTheDocument();
    } finally {
      harness.messages = [];
    }
  });

  it('keeps the quote in the box when sending fails', async () => {
    harness.messages = [
      { id: 'a1', role: 'assistant', content: 'Use port 8080 for the server.' },
    ] as ChatMessage[];
    apiPost.mockImplementation((url: string) => (url === '/agents/chat' ? Promise.reject(new Error('down')) : Promise.resolve({ data: {} })));
    try {
      const user = userEvent.setup();
      renderHome();
      await waitFor(() => expect(document.querySelector('[data-reply-source] p')).not.toBeNull());
      const range = document.createRange();
      range.selectNodeContents(document.querySelector('[data-reply-source] p')!);
      window.getSelection()!.removeAllRanges();
      window.getSelection()!.addRange(range);
      fireEvent.pointerUp(document);
      fireEvent.click(await screen.findByRole('button', { name: 'Reply' }));
      await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'why?{enter}');

      await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat', expect.objectContaining({
        message: '> Use port 8080 for the server.\n\nwhy?',
      })));
      expect(await screen.findByRole('button', { name: 'Remove quote' })).toBeInTheDocument();
      expect(screen.getByRole('combobox', { name: 'Message the agent' })).toHaveValue('why?');
    } finally {
      harness.messages = [];
    }
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

  it('sends the reasoning level picked in the + menu', async () => {
    const user = userEvent.setup();
    renderHome();
    await user.click(screen.getByRole('button', { name: 'More options' }));
    await user.click(screen.getByRole('radio', { name: 'high' }));
    await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'think hard{enter}');

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat', expect.objectContaining({
      message: 'think hard',
      reasoning_effort: 'high',
    })));
    expect(localStorage.getItem('chatReasoningEffort')).toBe('high');
  });

  it('uses task mode only for the Run as task action', async () => {
    const user = userEvent.setup();
    renderHome();
    await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'prepare a report');
    await user.click(screen.getByRole('button', { name: 'More options' }));
    await user.click(screen.getByRole('menuitem', { name: 'Run as task' }));

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

    fireEvent.click(await screen.findByRole('button', { name: 'More options' }));
    expect(await screen.findByRole('menuitem', { name: 'Run as task' })).toBeDisabled();
  });

  it('renders and resolves an interactive question from the chat stream', async () => {
    const user = userEvent.setup();
    renderHome();
    await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'Help me choose');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    act(() => harness.onMessage?.({
      type: 'question_request',
      request_id: 'question-1',
      session_id: 'session-1',
      prompt: 'Run this in the background?',
      details: 'You can keep chatting while it runs.',
      options: [
        { id: 'background', label: 'Run in background' },
        { id: 'chat', label: 'Keep in chat' },
      ],
      allow_free_text: false,
      recommended_option_id: 'background',
      auto_submit_seconds: null,
      auto_submit_at: null,
    }));

    await user.click(screen.getByRole('radio', { name: /Run in background/i }));
    await user.click(screen.getByRole('button', { name: 'Submit answer' }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/question', {
      request_id: 'question-1',
      action: 'answer',
      answers: [{ question_id: 'q1', option_ids: ['background'] }],
    }));
    expect(screen.queryByRole('group', { name: 'Run this in the background?' })).not.toBeInTheDocument();
  });
  it('answers an open question with words typed in the chat box', async () => {
    const user = userEvent.setup();
    renderHome();
    await user.type(screen.getByRole('combobox', { name: 'Message the agent' }), 'Help me choose');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    act(() => harness.onMessage?.({
      type: 'question_request',
      request_id: 'question-2',
      session_id: 'session-1',
      prompt: 'Which one?',
      options: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }],
      allow_free_text: false,
      recommended_option_id: null,
    }));
    const box = await screen.findByRole('combobox', { name: 'Message the agent' });
    expect(box).toHaveAttribute('placeholder', 'Answer above, or type here to reply instead…');
    await user.type(box, 'Neither, try C');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/question', {
      request_id: 'question-2',
      action: 'answer',
      text: 'Neither, try C',
    }));
  });
});
