import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TaskPage from '@/pages/TaskPage';

const harness = vi.hoisted(() => ({
  resources: new Map<string, { data?: unknown; loading?: boolean; error?: string | null }>(),
  apiPost: vi.fn(),
  apiDelete: vi.fn(),
}));

vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => {
    const state = path ? harness.resources.get(path) : undefined;
    return {
      data: state?.data,
      loading: state?.loading ?? false,
      error: state?.error ?? null,
      refetch: vi.fn(),
    };
  },
}));

vi.mock('@/lib/api', () => ({
  api: { post: harness.apiPost, delete: harness.apiDelete },
  errorMessage: (_error: unknown, fallback: string) => fallback,
}));

function renderTaskPage(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Routes>
        <Route path="/tasks/new" element={<TaskPage />} />
        <Route path="/tasks/:taskId/edit" element={<TaskPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('TaskPage assignment prefill', () => {
  beforeEach(() => {
    harness.resources.clear();
    harness.resources.set('/agents', {
      data: [
        { id: 'agent-1', name: 'Agent One', llm: { provider: 'openai', model: 'gpt-4o' } },
        { id: 'agent-2', name: 'Agent Two', llm: { provider: 'google', model: 'gemini' } },
      ],
    });
    harness.resources.set('/teams', {
      data: [{ id: 'team-1', name: 'Team One', assigned_agents: ['agent-1', 'agent-2'] }],
    });
    harness.apiPost.mockReset().mockResolvedValue({ data: {} });
    harness.apiDelete.mockReset().mockResolvedValue({ data: {} });
  });

  it('prefills a valid agent in create mode', async () => {
    renderTaskPage('/tasks/new?agentId=agent-1');

    await waitFor(() => expect(screen.getByRole('checkbox', { name: /Agent One/ })).toBeChecked());
    expect(screen.getByRole('checkbox', { name: /Agent Two/ })).not.toBeChecked();
  });

  it('prefills a valid team and its current members in create mode', async () => {
    renderTaskPage('/tasks/new?teamId=team-1');

    await waitFor(() => expect(screen.getByRole('combobox', { name: /TEAM/ })).toHaveValue('team-1'));
    expect(screen.getByRole('checkbox', { name: /Agent One/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Agent Two/ })).toBeChecked();
  });

  it('prefills an agent even when the unrelated team request fails', async () => {
    harness.resources.set('/teams', { error: 'Teams are unavailable.' });

    renderTaskPage('/tasks/new?agentId=agent-1');

    await waitFor(() => expect(screen.getByRole('checkbox', { name: /Agent One/ })).toBeChecked());
    expect(screen.getByRole('alert')).toHaveTextContent('Teams are unavailable.');
  });

  it('prefills a team even when the unrelated agent request fails', async () => {
    harness.resources.set('/agents', { error: 'Agents are unavailable.' });

    renderTaskPage('/tasks/new?teamId=team-1');

    await waitFor(() => expect(screen.getByRole('combobox', { name: /TEAM/ })).toHaveValue('team-1'));
    expect(screen.getByRole('alert')).toHaveTextContent('Agents are unavailable.');
  });

  it('ignores invalid create-mode assignment IDs', async () => {
    renderTaskPage('/tasks/new?teamId=missing&agentId=also-missing');

    await waitFor(() => expect(screen.getByRole('combobox', { name: /TEAM/ })).toHaveValue(''));
    expect(screen.getByRole('checkbox', { name: /Agent One/ })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Agent Two/ })).not.toBeChecked();
  });

  it('ignores prefill parameters while editing an existing task', async () => {
    harness.resources.set('/tasks/task-1', {
      data: {
        id: 'task-1',
        title: 'Existing Task',
        description: 'Keep stored assignment.',
        assigned_agents: ['agent-2'],
        team_id: null,
        step_instructions: {},
      },
    });

    renderTaskPage('/tasks/task-1/edit?agentId=agent-1&teamId=team-1');

    await waitFor(() => expect(screen.getByRole('checkbox', { name: /Agent Two/ })).toBeChecked());
    expect(screen.getByRole('checkbox', { name: /Agent One/ })).not.toBeChecked();
    expect(screen.getByRole('combobox', { name: /TEAM/ })).toHaveValue('');
  });
});
