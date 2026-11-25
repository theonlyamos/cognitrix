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
      needsSetup: false,
      dismiss: vi.fn(),
      start: voice.start,
      stop: vi.fn(async () => undefined),
      cancel: voice.cancel,
    };
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
});
