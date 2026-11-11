import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Dashboard from './Dashboard';
import { CHAT_DRAFT_KEY } from '@/lib/chatDraft';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get: apiGet, post: apiPost },
}));

vi.mock('@/context/AppContext', () => ({ useUser: () => ({ user: { name: 'Ada Lovelace', email: 'a@example.com' } }) }));
vi.mock('@/context/NotificationsContext', () => ({ useOptionalNotifications: () => ({ inbox: [], inboxLoaded: true }) }));

vi.mock('@/hooks/useVoiceRecorder', async () => {
  const { useState } = await import('react');
  return {
    micErrorMessage: () => 'Microphone access is blocked.',
    useVoiceRecorder: () => {
      const [state, setState] = useState<'idle' | 'recording'>('idle');
      return {
        state,
        elapsed: 2,
        analyser: null,
        start: async () => setState('recording'),
        stop: async () => { setState('idle'); return new Blob(['x']); },
        cancel: () => setState('idle'),
      };
    },
  };
});

const DATA: Record<string, unknown> = {
  '/agents': [{ id: 'a1', name: 'Assistant', llm: { provider: 'anthropic', model: 'claude-sonnet-5-5' } }],
  '/sessions/chats': [],
  '/tasks': [],
  '/automations': [],
  '/agents/runs': { runs: [] },
  '/usage?days=14': { prices_available: false, total: { prompt_tokens: 0, completion_tokens: 0, cost: 0 }, by_day: [], by_model: [] },
  '/audio/settings': { provider: '', model: '', effective: { provider: 'groq', model: 'w', ready: true }, max_seconds: 600, providers: [] },
};

function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/home" element={<div>chat page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Dashboard', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiGet.mockImplementation(async (path: string) => ({ data: DATA[path] ?? [] }));
    sessionStorage.clear();
  });

  it('greets the person and shows empty states', async () => {
    renderDashboard();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Ada');
    expect(await screen.findByText("You're all caught up.")).toBeInTheDocument();
    expect(await screen.findByText(/No chats yet/)).toBeInTheDocument();
  });

  it('puts a spoken question in the ask bar, then opens chat with it', async () => {
    apiPost.mockResolvedValue({ data: { text: 'summarise my week' } });
    renderDashboard();
    await screen.findByRole('option', { name: 'Assistant' });
    fireEvent.click(screen.getByRole('button', { name: 'Voice input' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Stop recording' }));
    await waitFor(() => expect(screen.getByLabelText('Ask an agent')).toHaveValue('summarise my week'));
    expect(apiPost.mock.calls[0][0]).toBe('/audio/transcriptions');

    fireEvent.click(screen.getByRole('button', { name: 'Start chat' }));
    expect(await screen.findByText('chat page')).toBeInTheDocument();
    expect(sessionStorage.getItem(CHAT_DRAFT_KEY)).toBe('summarise my week');
  });
});
