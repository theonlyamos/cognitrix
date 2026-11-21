import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Tasks from './Tasks';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api,
}));

const watching = {
  id: 'w1', repo: 'acme/app', number: 7, title: 'Add login', url: 'https://github.com/acme/app/pull/7',
  target: 'chat', session_id: 's1', task_id: null, where: 'chat "Release prep"', mode: 'poll', check_seconds: 120, next_check_at: '2030-06-01 12:02:00',
  note: null, status: 'watching', stop_reason: null, error: null, last_news_at: null,
};
const viaApp = { ...watching, id: 'w3', number: 9, title: 'Docs', mode: 'app', check_seconds: null };
const merged = {
  ...watching, id: 'w2', number: 6, title: 'Fix build', target: 'task', session_id: null, task_id: 't1',
  where: 'task "Babysit"', mode: 'webhook', status: 'stopped', stop_reason: 'The pull request was merged.',
};

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  api.get.mockImplementation(async (url: string) => ({ data: url === '/github/watches' ? [watching, viaApp, merged] : [] }));
  api.post.mockResolvedValue({ data: {} });
  api.delete.mockResolvedValue({ data: {} });
});

describe('Tasks: watched pull requests', () => {
  it('lists watches with where news goes, and stops or removes them', async () => {
    render(<MemoryRouter><Tasks /></MemoryRouter>);
    const live = (await screen.findByText('acme/app#7', { exact: false })).closest('li')!;
    expect(live).toHaveTextContent('every 2 min');
    expect(live).toHaveTextContent('Next check');
    expect(screen.getByText('acme/app#9', { exact: false }).closest('li')!).toHaveTextContent('instant');
    expect(within(live).getByRole('link', { name: 'chat "Release prep"' })).toHaveAttribute('href', '/home?chat=s1');
    const done = screen.getByText('acme/app#6', { exact: false }).closest('li')!;
    expect(done).toHaveTextContent('Stopped: The pull request was merged.');
    expect(within(done).getByRole('link', { name: 'task "Babysit"' })).toHaveAttribute('href', '/tasks/t1');

    fireEvent.click(within(live).getByRole('button', { name: 'Stop watching' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/github/watches/w1/stop'));
    fireEvent.click(within(done).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/github/watches/w2'));
  });
});

describe('Tasks: the list', () => {
  const tasks = [
    { id: 'a', title: 'Weekly digest', status: 'pending', assigned_agents: ['w'], schedule_cron: '0 9 * * 1', schedule_enabled: true, next_run_at: '2030-06-03 09:00:00' },
    { id: 'b', title: 'Compare platforms', status: 'completed', assigned_agents: ['w'], run_status: 'completed' },
    { id: 'c', title: 'Summarise mail', status: 'in_progress', assigned_agents: ['w'], run_status: 'running' },
    { id: 'd', title: 'Check-ins: Assistant', status: 'completed', standing_agent_id: 'w', schedule_interval: 14400, schedule_enabled: true },
  ];
  beforeEach(() => {
    api.get.mockImplementation(async (url: string) => ({
      data: url === '/tasks' ? tasks : url === '/agents' ? [{ id: 'w', name: 'Writer' }] : [],
    }));
  });

  it('says statuses and schedules in words, and leaves check-ins to the agent page', async () => {
    render(<MemoryRouter><Tasks /></MemoryRouter>);
    const digest = (await screen.findByText('Weekly digest')).closest('a')!;
    expect(digest).toHaveTextContent(/Every Monday at/);
    expect(digest).toHaveTextContent('Scheduled');
    expect(digest).not.toHaveTextContent('0 9 * * 1');
    expect(screen.getByText('Summarise mail').closest('a')!).toHaveTextContent('Running');
    expect(screen.getByText('Compare platforms').closest('a')!).toHaveTextContent('Done');
    expect(screen.queryByText('Check-ins: Assistant')).not.toBeInTheDocument();
    expect(screen.queryByText('in_progress')).not.toBeInTheDocument();
  });

  it('filters by what is running or scheduled', async () => {
    render(<MemoryRouter><Tasks /></MemoryRouter>);
    await screen.findByText('Weekly digest');
    fireEvent.click(screen.getByRole('tab', { name: /Running/ }));
    expect(screen.queryByText('Weekly digest')).not.toBeInTheDocument();
    expect(screen.getByText('Summarise mail')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /Scheduled/ }));
    expect(screen.getByText('Weekly digest')).toBeInTheDocument();
    expect(screen.queryByText('Summarise mail')).not.toBeInTheDocument();
  });
});
