import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path === '/agents' ? [{ id: 'agent-1', name: 'Assistant' }] : [],
    loading: false,
    loadedPath: path,
    error: null,
    refetch: vi.fn(),
  }),
}));

const voice = vi.hoisted(() => ({
  onText: null as null | ((text: string) => void),
  start: vi.fn(async () => undefined),
  cancel: vi.fn(),
  status: 'idle' as string,
  needsSetup: false,
}));

vi.mock('@/components/VoiceInput', () => ({
  Waveform: () => <div data-testid="wave" />,
  useVoiceInput: ({ onText }: { onText: (text: string) => void }) => {
    voice.onText = onText;
    return {
      status: voice.status,
      active: voice.status === 'recording',
      elapsed: 0,
      maxSeconds: 600,
      analyser: null,
      error: null,
      needsSetup: voice.needsSetup,
      dismiss: vi.fn(),
      start: voice.start,
      stop: vi.fn(async () => undefined),
      cancel: voice.cancel,
    };
  },
}));

const desktopEvents = vi.hoisted(() => new Map<string, () => void>());

vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  onDesktopEvent: async (event: string, handler: () => void) => {
    desktopEvents.set(event, handler);
    return () => desktopEvents.delete(event);
  },
}));

import QuickAsk from './QuickAsk';

function sse(...events: Array<[string, Record<string, string>]>) {
  const body = events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('');
  return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

describe('QuickAsk', () => {
  beforeEach(() => {
    localStorage.clear();
    voice.status = 'idle';
    voice.needsSetup = false;
    voice.start.mockClear();
    voice.cancel.mockClear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it('opens listening, with a button to switch to the keyboard', async () => {
    render(<QuickAsk />);
    await waitFor(() => expect(voice.start).toHaveBeenCalled());
    expect(screen.queryByRole('textbox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /type instead/i }));
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    expect(voice.cancel).toHaveBeenCalled();
    expect(localStorage.getItem('quickAskMode')).toBe('keyboard');
  });

  it('remembers the keyboard and does not open the mic', () => {
    localStorage.setItem('quickAskMode', 'keyboard');
    render(<QuickAsk />);
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    expect(voice.start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /talk instead/i }));
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(localStorage.getItem('quickAskMode')).toBe('voice');
  });

  it('switches to the keyboard when you start typing, keeping the letter', () => {
    render(<QuickAsk />);
    fireEvent.keyDown(document, { key: 'h' });
    expect(screen.getByRole('textbox')).toHaveValue('h');
  });

  it('asks the agent what was said and shows the answer as a card', async () => {
    const fetchMock = vi.fn(async () => sse(['chunk', { content: 'You have **3** open pull requests.' }]));
    vi.stubGlobal('fetch', fetchMock);
    render(<QuickAsk />);
    await act(async () => voice.onText?.('How many open PRs?'));
    expect(await screen.findByText('How many open PRs?')).toBeInTheDocument();
    expect(await screen.findByText(/open pull requests/)).toBeInTheDocument();
    const body = JSON.parse((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
    expect(body.message).toBe('How many open PRs?');
  });

  it('keeps the voice pill and offers setup when speech-to-text is not set up', () => {
    voice.needsSetup = true;
    render(<QuickAsk />);
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByRole('button', { name: /set up voice/i })).toBeInTheDocument();
    expect(voice.start).not.toHaveBeenCalled();
  });

  it('starts a new chat when the one it was using is gone', async () => {
    let calls = 0;
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      calls += 1;
      const sent = JSON.parse(init.body as string);
      if (calls === 1) return sse(['chunk', { content: 'First answer.', session_id: 'old' }]);
      if (sent.session_id === 'old') return new Response(JSON.stringify({ detail: 'Session not found' }), { status: 404 });
      return sse(['chunk', { content: 'Fresh answer.', session_id: 'new' }]);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<QuickAsk />);
    await act(async () => voice.onText?.('One'));
    expect(await screen.findByText('First answer.')).toBeInTheDocument();
    await act(async () => voice.onText?.('Two'));
    expect(await screen.findByText('Fresh answer.')).toBeInTheDocument();
    const bodies = fetchMock.mock.calls.map((c) => JSON.parse((c[1] as RequestInit).body as string).session_id);
    expect(bodies).toEqual([null, 'old', null]);
  });

  it('starts a new chat on every Alt+Space, but keeps it for follow-ups', async () => {
    let calls = 0;
    const fetchMock = vi.fn(async () => {
      calls += 1;
      return sse(['chunk', { content: `Answer ${calls}.`, session_id: calls < 3 ? 'first' : 'second' }]);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<QuickAsk />);
    await act(async () => voice.onText?.('One'));
    expect(await screen.findByText('Answer 1.')).toBeInTheDocument();
    await act(async () => voice.onText?.('Two'));
    expect(await screen.findByText('Answer 2.')).toBeInTheDocument();
    await act(async () => desktopEvents.get('quick-ask-open')?.());
    expect(screen.queryByText('Answer 1.')).toBeNull();
    await act(async () => voice.onText?.('Three'));
    expect(await screen.findByText('Answer 3.')).toBeInTheDocument();
    await act(async () => voice.onText?.('Four'));
    expect(await screen.findByText('Answer 4.')).toBeInTheDocument();
    const sessions = fetchMock.mock.calls.map((c) => JSON.parse(((c as unknown as [string, RequestInit])[1]).body as string).session_id);
    expect(sessions).toEqual([null, 'first', null, 'second']);
  });
});
