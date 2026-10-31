import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { blockedEndpoint, isRefusal } from './SafetyNotices';
import CommandPalette from './CommandPalette';
import Inbox from '@/pages/Inbox';
import { NotificationsProvider } from '@/context/NotificationsContext';
import { isDesktop } from '@/lib/desktop';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn().mockResolvedValue({ data: {} }));
vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost },
  API_BASE: '/api/v1',
  errorMessage: (_err: unknown, fallback: string) => fallback,
}));

vi.mock('@/context/AppContext', () => ({
  useUser: () => ({ user: { id: 'u1', name: 'Amos', email: 'a@example.com' }, isLoading: false, login: vi.fn(), logout: vi.fn(), checkAuth: vi.fn() }),
}));

const approval = {
  kind: 'approval',
  id: 'ap-1',
  run_id: 'run-1',
  task_id: 'task-1',
  step_index: 4,
  tool_name: 'Bash',
  params: { command: 'git push -u origin fix/base-url-allowlist' },
  risk_level: 'high',
  details: 'pushes a branch',
  status: 'pending',
  created_at: '2026-10-31T12:00:00',
  task_title: 'Audit API key handling',
};

function routeGet(inboxItems: unknown[]) {
  apiGet.mockImplementation((path: string) => {
    if (path === '/inbox') return Promise.resolve({ data: { items: inboxItems, count: inboxItems.length } });
    if (path === '/agents') return Promise.resolve({ data: [{ id: 'a1', name: 'Researcher', llm: { provider: 'anthropic', model: 'claude' } }] });
    if (path === '/tasks') return Promise.resolve({ data: [{ id: 't1', title: 'Provider test matrix', status: 'completed' }] });
    if (path === '/teams') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: [] });
  });
}

describe('desktop shell', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockClear();
    localStorage.clear();
  });

  it('is a plain web app outside the desktop shell', () => {
    expect(isDesktop()).toBe(false);
  });

  it('recognises refused commands and blocked provider addresses', () => {
    expect(isRefusal("Error: Command refused - '/etc' is outside the working folder '/home/me/Cognitrix'.")).toBe(true);
    expect(isRefusal("Error: Path '/etc/passwd' is outside the working directory")).toBe(true);
    expect(isRefusal('total 0')).toBe(false);
    expect(blockedEndpoint("base_url 'http://169.254.169.254/v1' is not a trusted endpoint for the server's openai key")).toBe(
      'http://169.254.169.254/v1',
    );
    expect(blockedEndpoint('all good')).toBeNull();
  });

  it('command palette filters actions, agents and tasks', async () => {
    routeGet([]);
    render(
      <MemoryRouter>
        <CommandPalette open onClose={() => {}} />
      </MemoryRouter>,
    );
    expect(await screen.findByText('New chat with Researcher')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox', { name: /search commands/i }), 'matrix');
    expect(screen.getByText('Provider test matrix')).toBeInTheDocument();
    expect(screen.queryByText('New chat with Researcher')).not.toBeInTheDocument();
  });

  it('inbox shows a paused run and approves it', async () => {
    routeGet([approval]);
    render(
      <MemoryRouter>
        <NotificationsProvider>
          <Inbox />
        </NotificationsProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText('The agent wants to run Bash')).toBeInTheDocument();
    expect(screen.getByText(/git push -u origin/)).toBeInTheDocument();
    expect(screen.getAllByText(/Audit API key handling · step 5/).length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith('/tasks/task-1/runs/run-1/approvals/ap-1', { approved: true }),
    );
  });
});
