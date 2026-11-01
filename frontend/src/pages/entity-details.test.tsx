import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AgentDetail from '@/pages/AgentDetail';
import AgentPage from '@/pages/AgentPage';
import TeamDetail from '@/pages/TeamDetail';
import TeamPage from '@/pages/TeamPage';

const appSource = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8');

const harness = vi.hoisted(() => ({
  resources: new Map<string, { data?: unknown; loading?: boolean; error?: string | null }>(),
  refetch: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  apiGet: vi.fn(),
  apiPut: vi.fn(),
}));

vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => {
    const state = path ? harness.resources.get(path) : undefined;
    return {
      data: state?.data,
      loading: state?.loading ?? false,
      error: state?.error ?? null,
      refetch: harness.refetch,
      setData: vi.fn(),
    };
  },
}));

vi.mock('@/lib/api', () => ({
  api: { get: harness.apiGet, put: harness.apiPut, post: harness.apiPost, patch: harness.apiPatch, delete: harness.apiDelete },
  errorMessage: (_error: unknown, fallback: string) => fallback,
}));

function renderAgentDetail(path = '/agents/agent-1') {
  return render(
    <MemoryRouter initialEntries={[path]} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Routes>
        <Route path="/agents/:agentId" element={<AgentDetail />} />
        <Route path="/agents/:agentId/:tab" element={<AgentDetail />} />
        <Route path="/agents" element={<div>Agent list</div>} />
        <Route path="/home" element={<div>Chat home</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderTeamDetail() {
  return render(
    <MemoryRouter initialEntries={['/teams/team-1']} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Routes>
        <Route path="/teams/:teamId" element={<TeamDetail />} />
        <Route path="/teams" element={<div>Team list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderAgentNew() {
  return render(
    <MemoryRouter initialEntries={['/agents/new']} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Routes>
        <Route path="/agents/new" element={<AgentPage />} />
        <Route path="/agents/:agentId" element={<div>Agent details destination</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderTeamEdit() {
  return render(
    <MemoryRouter initialEntries={['/teams/team-1/edit']} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Routes>
        <Route path="/teams/:teamId/edit" element={<TeamPage />} />
        <Route path="/teams/:teamId" element={<div>Team details destination</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('entity detail routes', () => {
  beforeEach(() => {
    harness.resources.clear();
    harness.resources.set('/agents/agent-1', {
      data: {
        id: 'agent-1',
        name: 'Agent One',
        system_prompt: 'Be precise.',
        llm: { provider: 'openai', model: 'gpt-4o', temperature: 0.2, max_tokens: 4096 },
        tools: [{ name: 'search', description: 'Search the web' }],
      },
    });
    harness.resources.set('/agents/agent-1/overview', {
      data: {
        stats: { chats_week: 3, running_chats: 1, task_runs_week: 2, failed_runs_week: 0, pending_approvals: 0 },
        recent_chats: [{ id: 'chat-9', title: 'Weekly report', last_active: '2026-11-01T10:00:00', running: true }],
        tasks: [{ id: 'task-1', title: 'Agent Assigned Task', status: 'pending', schedule_enabled: false }],
        approvals: [],
        teams: [{ id: 'team-1', name: 'Team One', leader_id: 'agent-1', members: [{ id: 'agent-1', name: 'Agent One' }, { id: 'agent-2', name: 'Agent Two' }] }],
      },
    });
    harness.resources.set('/agents/options', {
      data: { reviewer: { mode: 'off', provider: '', model: '', max_allow: 'medium' }, coding_clis: [], default_max_runs_per_day: 24 },
    });
    harness.resources.set('/automations', { data: [] });
    harness.resources.set('/teams/team-1', {
      data: {
        id: 'team-1',
        name: 'Team One',
        description: 'Ships reliable work.',
        assigned_agents: ['agent-1'],
        leader_id: 'agent-1',
      },
    });
    harness.resources.set('/agents', {
      data: [{ id: 'agent-1', name: 'Agent One', llm: { provider: 'openai', model: 'gpt-4o' } }],
    });
    harness.resources.set('/tasks', {
      data: [
        {
          id: 'task-1',
          title: 'Agent Assigned Task',
          description: 'Already assigned to the agent.',
          assigned_agents: ['agent-1'],
          team_id: null,
          status: 'pending',
        },
        {
          id: 'task-2',
          title: 'Task Two',
          description: 'Preserve this description.',
          assigned_agents: ['agent-existing'],
          team_id: 'team-existing',
          status: 'pending',
        },
        {
          id: 'task-3',
          title: 'Team Assigned Task',
          description: 'Already assigned to the team.',
          assigned_agents: ['agent-1'],
          team_id: 'team-1',
          status: 'completed',
        },
        {
          id: 'task-4',
          title: 'Failed Team Task',
          description: 'Needs attention.',
          assigned_agents: [],
          team_id: 'team-1',
          status: 'failed',
        },
      ],
    });
    harness.resources.set('/tools', { data: [] });
    harness.refetch.mockReset();
    harness.apiPost.mockReset().mockResolvedValue({ data: {} });
    harness.apiPatch.mockReset().mockResolvedValue({ data: {} });
    harness.apiDelete.mockReset().mockResolvedValue({ data: {} });
    harness.apiGet.mockReset().mockResolvedValue({ data: { models: [], live: false } });
    harness.apiPut.mockReset().mockResolvedValue({ data: {} });
    localStorage.clear();
    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  it('separates canonical detail routes from edit forms', () => {
    expect(appSource).toContain('const AgentDetail = lazy');
    expect(appSource).toContain('const TeamDetail = lazy');
    expect(appSource).toContain('<Route path="/agents/:agentId" element={<AgentDetail />} />');
    expect(appSource).toContain('<Route path="/agents/:agentId/:tab" element={<AgentDetail />} />');
    expect(appSource).not.toContain('<Route path="/agents/:agentId/edit" element={<AgentPage />} />');
    expect(appSource).toContain('<Route path="/teams/:teamId" element={<TeamDetail />} />');
    expect(appSource).toContain('<Route path="/teams/:teamId/edit" element={<TeamPage />} />');
    expect(appSource).not.toContain('/interact');
  });

  it('keeps the agent detail header on one mobile line and moves secondary actions into a sheet', async () => {
    renderAgentDetail();

    const header = screen.getByRole('navigation', { name: 'Breadcrumb' }).closest('header');
    expect(header).toHaveClass('flex-row', 'flex-nowrap');
    expect(header).not.toHaveClass('overflow-hidden');

    await userEvent.click(screen.getByRole('button', { name: 'More actions' }));
    const sheet = screen.getByRole('dialog', { name: 'Page actions' });
    expect(within(sheet).getByRole('link', { name: 'New task' })).toHaveAttribute('href', '/tasks/new?agentId=agent-1');
    expect(within(sheet).getByRole('button', { name: 'Delete agent' })).toBeInTheDocument();
  });

  it('keeps the team detail header on one mobile line and moves secondary actions into a sheet', async () => {
    renderTeamDetail();

    const header = screen.getByRole('heading', { name: 'Team One' }).closest('header');
    expect(header).toHaveClass('flex-row', 'flex-nowrap');
    expect(header).not.toHaveClass('overflow-hidden');

    await userEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(screen.getByRole('dialog', { name: 'Page actions' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Edit team' })).toHaveAttribute('href', '/teams/team-1/edit');
    expect(screen.getByRole('button', { name: 'Delete team' })).toBeInTheDocument();
  });

  it('opens on the overview with stats, chats and teams, and starts a blank chat', async () => {
    renderAgentDetail();

    expect(screen.getByRole('heading', { name: 'Agent One' })).toBeInTheDocument();
    expect(screen.getByText('gpt-4o')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByRole('tab')).toHaveLength(7);
    expect(screen.getByText('1 chat running')).toBeInTheDocument();
    expect(screen.getByText('Weekly report')).toBeInTheDocument();
    expect(screen.getByText('leads Team One')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Chat with agent/ }));

    expect(localStorage.getItem('selectedAgentId')).toBe('agent-1');
    expect(localStorage.getItem('chatSession:agent-1')).toBe('');
    expect(screen.getByText('Chat home')).toBeInTheDocument();
  });

  it('opens a recent chat from the overview', async () => {
    renderAgentDetail();
    await userEvent.click(screen.getByRole('button', { name: /Weekly report/ }));
    expect(localStorage.getItem('chatSession:agent-1')).toBe('chat-9');
  });

  it('sends the old edit link to the General tab', () => {
    renderAgentDetail('/agents/agent-1/edit');
    expect(screen.getByRole('tab', { name: 'General' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('Name')).toHaveValue('Agent One');
  });

  it('keeps edits across tabs and saves them together', async () => {
    harness.apiPost.mockResolvedValueOnce({ data: { id: 'agent-1', name: 'Agent Prime' } });
    renderAgentDetail('/agents/agent-1/general');

    const name = screen.getByLabelText('Name');
    await userEvent.clear(name);
    await userEvent.type(name, 'Agent Prime');
    expect(screen.getByText('@Agent-Prime')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: 'Teams' }));
    await userEvent.click(screen.getByRole('checkbox', { name: /Let it create sub-agents/ }));
    await userEvent.click(screen.getByRole('tab', { name: 'Tasks' }));
    await userEvent.click(screen.getByRole('checkbox', { name: /schedule its own check-ins/ }));

    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(harness.apiPost).toHaveBeenCalledWith('/agents', expect.objectContaining({
      id: 'agent-1',
      name: 'Agent Prime',
      can_create_sub_agents: false,
      automation: expect.objectContaining({ self_checkins: false }),
    })));
  });

  it('discards unsaved edits', async () => {
    renderAgentDetail('/agents/agent-1/general');
    const name = screen.getByLabelText('Name');
    await userEvent.type(name, ' 2');
    await userEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.getByLabelText('Name')).toHaveValue('Agent One');
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
  });

  it('shows team roles and the group chat link', () => {
    renderAgentDetail('/agents/agent-1/teams');
    expect(screen.getByText('leader')).toBeInTheDocument();
    expect(screen.getByText('With @Agent-Two')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open group chat' })).toHaveAttribute('href', '/teams/team-1/chat');
  });

  it('deletes an agent from its detail header and returns to the list', async () => {
    renderAgentDetail();

    await userEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await userEvent.click(screen.getByRole('button', { name: 'Delete agent' }));

    await waitFor(() => expect(harness.apiDelete).toHaveBeenCalledWith('/agents/agent-1'));
    expect(screen.getByText('Agent list')).toBeInTheDocument();
  });

  it('renders team details with canonical edit and delete actions', async () => {
    renderTeamDetail();

    expect(screen.getByRole('heading', { name: 'Team One' })).toBeInTheDocument();
    expect(screen.getByText('Ships reliable work.')).toBeInTheDocument();
    expect(screen.getByText('Agent One')).toBeInTheDocument();
    expect(screen.getByText('LEAD')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Edit team' })).toHaveAttribute('href', '/teams/team-1/edit');
    expect(screen.getByRole('link', { name: 'Failed Team Task', description: 'failed' })).toBeInTheDocument();
    expect(screen.getByText('failed')).toHaveClass('text-danger-ink');

    await userEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await userEvent.click(screen.getByRole('button', { name: 'Delete team' }));

    await waitFor(() => expect(harness.apiDelete).toHaveBeenCalledWith('/teams/team-1'));
    expect(screen.getByText('Team list')).toBeInTheDocument();
  });

  it('assigns existing tasks to an agent without disturbing other fields', async () => {
    renderAgentDetail('/agents/agent-1/tasks');

    expect(screen.getByRole('link', { name: /Agent Assigned Task/ })).toHaveAttribute('href', '/tasks/task-1');
    expect(screen.getByRole('link', { name: 'New task' })).toHaveAttribute('href', '/tasks/new?agentId=agent-1');

    await userEvent.click(screen.getByRole('button', { name: 'Assign task' }));
    await userEvent.click(screen.getByRole('checkbox', { name: /Task Two/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Assign selected' }));

    await waitFor(() => expect(harness.apiPatch).toHaveBeenCalledWith('/tasks/task-2/assignment', {
      assigned_agents: ['agent-existing', 'agent-1'],
      team_id: 'team-existing',
    }));
    expect(harness.apiPost).not.toHaveBeenCalled();
    expect(harness.refetch).toHaveBeenCalled();
  });

  it('assigns existing tasks to a team and synchronizes current members', async () => {
    renderTeamDetail();

    expect(screen.getByRole('link', { name: 'Team Assigned Task' })).toHaveAttribute('href', '/tasks/task-3');
    expect(screen.getByRole('link', { name: 'New task' })).toHaveAttribute('href', '/tasks/new?teamId=team-1');

    await userEvent.click(screen.getByRole('button', { name: 'Assign task' }));
    await userEvent.click(screen.getByRole('checkbox', { name: /Task Two/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Assign selected' }));

    await waitFor(() => expect(harness.apiPatch).toHaveBeenCalledWith('/tasks/task-2/assignment', {
      assigned_agents: ['agent-1'],
      team_id: 'team-1',
    }));
    expect(harness.apiPost).not.toHaveBeenCalled();
    expect(harness.refetch).toHaveBeenCalled();
  });

  it('keeps assignment selection open when saving fails', async () => {
    harness.apiPatch.mockRejectedValueOnce(new Error('offline'));
    renderAgentDetail('/agents/agent-1/tasks');

    await userEvent.click(screen.getByRole('button', { name: 'Assign task' }));
    const task = screen.getByRole('checkbox', { name: /Task Two/ });
    await userEvent.click(task);
    await userEvent.click(screen.getByRole('button', { name: 'Assign selected' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not assign the selected tasks.');
    expect(task).toBeChecked();
  });

  it('labels the assignment disclosure, moves focus, and disables choices while saving', async () => {
    let resolvePatch!: (value: { data: object }) => void;
    harness.apiPatch.mockReturnValueOnce(new Promise((resolve) => { resolvePatch = resolve; }));
    renderAgentDetail('/agents/agent-1/tasks');

    const trigger = screen.getByRole('button', { name: 'Assign task' });
    await userEvent.click(trigger);
    const task = screen.getByRole('checkbox', { name: /Task Two/ });

    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('group', { name: 'Tasks available to assign' })).toBeInTheDocument();
    await waitFor(() => expect(task).toHaveFocus());

    await userEvent.click(task);
    await userEvent.click(screen.getByRole('button', { name: 'Assign selected' }));
    await waitFor(() => expect(task).toBeDisabled());

    resolvePatch({ data: {} });
    await waitFor(() => expect(trigger).toHaveAttribute('aria-expanded', 'false'));
    expect(trigger).toHaveFocus();
  });

  it('shows a retryable task error instead of a false empty assignment state', async () => {
    harness.resources.set('/tasks', { error: 'Tasks are unavailable.' });
    renderAgentDetail('/agents/agent-1/tasks');

    expect(screen.getByRole('alert')).toHaveTextContent('Tasks are unavailable.');
    expect(screen.queryByRole('button', { name: 'Assign task' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Retry tasks' }));
    expect(harness.refetch).toHaveBeenCalled();
  });

  it('refreshes partial assignment successes and keeps only failed tasks selected', async () => {
    harness.resources.set('/tasks', {
      data: [
        { id: 'task-2', title: 'Task Two', assigned_agents: [], team_id: null, status: 'pending' },
        { id: 'task-4', title: 'Task Four', assigned_agents: [], team_id: null, status: 'pending' },
      ],
    });
    harness.apiPatch
      .mockResolvedValueOnce({ data: {} })
      .mockRejectedValueOnce(new Error('offline'));
    renderAgentDetail('/agents/agent-1/tasks');

    await userEvent.click(screen.getByRole('button', { name: 'Assign task' }));
    const succeeded = screen.getByRole('checkbox', { name: /Task Two/ });
    const failed = screen.getByRole('checkbox', { name: /Task Four/ });
    await userEvent.click(succeeded);
    await userEvent.click(failed);
    await userEvent.click(screen.getByRole('button', { name: 'Assign selected' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not assign 1 of 2 selected tasks.');
    expect(harness.refetch).toHaveBeenCalled();
    expect(succeeded).not.toBeChecked();
    expect(failed).toBeChecked();
  });

  it('offers Anthropic with recent models, a custom model name, and greys out temperature for new Claude models', async () => {
    harness.apiPost.mockResolvedValueOnce({ data: { id: 'agent-new' } });
    renderAgentNew();

    await userEvent.type(screen.getByPlaceholderText('Research Assistant'), 'Claude helper');
    const providerSelect = screen.getAllByRole('combobox').find((el) =>
      Array.from((el as HTMLSelectElement).options).some((o) => o.value === 'anthropic'),
    ) as HTMLSelectElement;
    expect(providerSelect).toBeDefined();
    await userEvent.selectOptions(providerSelect, 'anthropic');

    const modelSelect = screen.getAllByRole('combobox').find((el) =>
      Array.from((el as HTMLSelectElement).options).some((o) => o.value === 'claude-opus-5-5'),
    ) as HTMLSelectElement;
    expect(modelSelect).toHaveValue('claude-sonnet-5-5');
    expect(screen.getByLabelText('Temperature')).toBeDisabled();

    await userEvent.selectOptions(modelSelect, '__custom__');
    await userEvent.type(screen.getByLabelText('Custom model name'), 'claude-haiku-4-5');
    expect(screen.getByLabelText('Temperature')).toBeEnabled();

    await userEvent.click(screen.getByRole('button', { name: /Save/ }));
    await waitFor(() => expect(harness.apiPost).toHaveBeenCalledWith('/agents', expect.objectContaining({
      llm: expect.objectContaining({ provider: 'anthropic', model: 'claude-haiku-4-5', base_url: 'https://api.anthropic.com/v1' }),
    })));
  });

  it('returns team edits to the canonical detail page', async () => {
    renderTeamEdit();

    expect(screen.getByRole('link', { name: 'Cancel' })).toHaveAttribute('href', '/teams/team-1');
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(harness.apiPost).toHaveBeenCalledWith('/teams', expect.objectContaining({ id: 'team-1' })));
    expect(screen.getByText('Team details destination')).toBeInTheDocument();
  });
});
