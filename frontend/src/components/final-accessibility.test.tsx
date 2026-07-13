import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PageForm } from '@/components/form';
import { ThemeProvider } from '@/context/ThemeContext';
import AgentPage from '@/pages/AgentPage';
import ApiKeys from '@/pages/ApiKeys';
import Home from '@/pages/Home';
import Login from '@/pages/Login';
import Signup from '@/pages/Signup';
import TaskDetail from '@/pages/TaskDetail';
import TaskPage from '@/pages/TaskPage';

const harness = vi.hoisted(() => ({
  resources: new Map<
    string,
    { data?: unknown; loading?: boolean; error?: string | null }
  >(),
  refetch: vi.fn(),
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiDelete: vi.fn(),
  login: vi.fn(),
  messages: [] as unknown[],
  addMessage: vi.fn(),
  appendToLastMessage: vi.fn(),
  setIsStreaming: vi.fn(),
  addToolCall: vi.fn(),
  resolveToolCall: vi.fn(),
  clearMessages: vi.fn(),
  setMessages: vi.fn(),
  sseConnected: true,
  sseError: null as Error | null,
  sseOnMessage: null as null | ((event: Record<string, unknown>) => void),
  taskRunOnEvent: null as null | ((event: Record<string, unknown>) => void),
  taskRunConnected: true,
  taskRunError: null as Error | null,
  taskRunReconnect: vi.fn(),
  pollingEnabled: false,
  pollingCallback: null as null | (() => void),
  pollingIntervalMs: null as number | null,
}));

vi.mock('@/lib/api', () => ({
  api: {
    get: harness.apiGet,
    post: harness.apiPost,
    delete: harness.apiDelete,
  },
  errorMessage: (_error: unknown, fallback = 'Request failed.') => fallback,
}));

vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => {
    const state = path ? harness.resources.get(path) : undefined;
    return {
      data: state?.data,
      loading: state?.loading ?? false,
      error: state?.error ?? null,
      refetch: harness.refetch,
    };
  },
}));

vi.mock('@/hooks/useTaskRunEvents', () => ({
  useTaskRunEvents: ({
    onEvent,
  }: {
    onEvent: (event: Record<string, unknown>) => void;
  }) => {
    harness.taskRunOnEvent = onEvent;
    return {
      isConnected: harness.taskRunConnected,
      error: harness.taskRunError,
      reconnect: harness.taskRunReconnect,
    };
  },
}));

vi.mock('@/hooks/usePolling', () => ({
  usePolling: (
    callback: () => void,
    intervalMs: number,
    enabled: boolean,
  ) => {
    harness.pollingEnabled = enabled;
    harness.pollingCallback = callback;
    harness.pollingIntervalMs = intervalMs;
  },
}));

vi.mock('@/hooks/useSSE', () => ({
  useSSE: ({ onMessage }: { onMessage: (event: Record<string, unknown>) => void }) => {
    harness.sseOnMessage = onMessage;
    return {
      isConnected: harness.sseConnected,
      error: harness.sseError,
      reconnect: vi.fn(),
    };
  },
}));

vi.mock('@/context/AppContext', () => ({
  useUser: () => ({
    user: { id: 'user-1', name: 'Test User', email: 'test@example.com' },
    login: harness.login,
    logout: vi.fn(),
  }),
}));

vi.mock('@/context/SessionContext', () => ({
  useSession: () => ({
    messages: harness.messages,
    addMessage: harness.addMessage,
    appendToLastMessage: harness.appendToLastMessage,
    setIsStreaming: harness.setIsStreaming,
    addToolCall: harness.addToolCall,
    resolveToolCall: harness.resolveToolCall,
    clearMessages: harness.clearMessages,
    setMessages: harness.setMessages,
  }),
}));

function renderRoute(element: React.ReactElement, initialEntry = '/') {
  return render(
    <MemoryRouter
      initialEntries={[initialEntry]}
      future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
    >
      <ThemeProvider>{element}</ThemeProvider>
    </MemoryRouter>,
  );
}

function renderTaskPage() {
  return renderRoute(
    <Routes>
      <Route path="/tasks/new" element={<TaskPage />} />
    </Routes>,
    '/tasks/new',
  );
}

function renderTaskDetail() {
  return renderRoute(
    <Routes>
      <Route path="/tasks/:taskId" element={<TaskDetail />} />
    </Routes>,
    '/tasks/task-1',
  );
}

function renderAgentPage() {
  return renderRoute(
    <Routes>
      <Route path="/agents/new" element={<AgentPage />} />
    </Routes>,
    '/agents/new',
  );
}

function expectSiblingLiveStatus(button: HTMLElement, text: string) {
  expect(within(button).queryByRole('status')).not.toBeInTheDocument();
  const status = screen.getByRole('status');
  expect(status).toHaveClass('sr-only');
  expect(status).toHaveTextContent(text);
  expect(button.parentElement).toContainElement(status);
}

describe('final accessibility contracts', () => {
  beforeEach(() => {
    harness.resources.clear();
    harness.resources.set('/agents', {
      data: [{ id: 'agent-1', name: 'Agent One', llm: { provider: 'openai', model: 'gpt' } }],
    });
    harness.resources.set('/teams', {
      data: [{ id: 'team-1', name: 'Team One', assigned_agents: ['agent-1'] }],
    });
    harness.resources.set('/sessions/agents/agent-1?exclude_tasks=true', { data: [] });
    harness.refetch.mockReset();
    harness.apiGet.mockReset().mockResolvedValue({ data: [] });
    harness.apiPost.mockReset().mockResolvedValue({ data: {} });
    harness.apiDelete.mockReset().mockResolvedValue({ data: {} });
    harness.login.mockReset();
    harness.messages = [];
    harness.sseConnected = true;
    harness.sseError = null;
    harness.sseOnMessage = null;
    harness.taskRunOnEvent = null;
    harness.taskRunConnected = true;
    harness.taskRunError = null;
    harness.taskRunReconnect.mockReset();
    harness.pollingEnabled = false;
    harness.pollingCallback = null;
    harness.pollingIntervalMs = null;
    localStorage.clear();
    localStorage.setItem('selectedAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', '');
    Element.prototype.scrollIntoView = vi.fn();
  });

  it('names every TaskPage step and schedule control while retaining group names', async () => {
    renderTaskPage();

    expect(screen.getByRole('textbox', { name: /TITLE/ })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /DESCRIPTION/ })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: /STEPS/ })).toBeInTheDocument();

    const addStep = screen.getByRole('button', { name: /add step/i });
    expect(addStep).toHaveClass('min-h-11', 'md:min-h-0');
    await userEvent.click(addStep);

    expect(screen.getByRole('textbox', { name: 'Step 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove step 1' })).toHaveClass(
      'h-11',
      'w-11',
      'md:h-8',
      'md:w-8',
    );
    expect(screen.getByRole('combobox', { name: 'TEAM' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: /ASSIGNED AGENTS/ })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /^Agent One\b/ })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'SCHEDULE' })).toBeInTheDocument();

    const scheduleType = screen.getByRole('combobox', { name: 'Schedule type' });
    await userEvent.selectOptions(scheduleType, 'once');
    expect(screen.getByLabelText('Schedule time')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Schedule enabled' })).toBeInTheDocument();

    await userEvent.selectOptions(scheduleType, 'interval');
    expect(screen.getByRole('spinbutton', { name: 'Interval value' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Interval unit' })).toBeInTheDocument();

    await userEvent.selectOptions(scheduleType, 'cron');
    expect(screen.getByRole('textbox', { name: 'Cron expression' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Auto-start when created' })).toBeInTheDocument();
  });

  it('keeps the TaskPage schedule-enabled label touch-sized on mobile', async () => {
    renderTaskPage();
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Schedule type' }),
      'once',
    );

    expect(screen.getByRole('checkbox', { name: 'Schedule enabled' }).closest('label')).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
  });

  it('keeps the TaskPage auto-start label touch-sized on mobile', () => {
    renderTaskPage();

    expect(screen.getByRole('checkbox', { name: 'Auto-start when created' }).closest('label')).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
  });

  it('renders Home composer actions with responsive touch targets', () => {
    renderRoute(<Home />);

    expect(screen.getByRole('button', { name: /Summarize the benefits of unit testing\./ })).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
    expect(screen.getByRole('combobox', { name: 'Message the agent' })).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
    expect(screen.getByRole('button', { name: 'Send' })).toHaveClass(
      'h-11',
      'w-11',
      'md:h-8',
      'md:w-8',
    );
    expect(screen.getByRole('button', { name: 'Attach files' })).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
    expect(screen.getByRole('button', { name: /auto-approve/i })).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
  });

  it('renders TaskDetail step and synthesis selectors with responsive touch targets', async () => {
    harness.resources.set('/tasks/task-1', {
      data: { id: 'task-1', title: 'Task', status: 'completed' },
    });
    harness.resources.set('/tasks/task-1/runs', {
      data: [{
        id: 'run-1',
        status: 'completed',
        plan: [{ index: 0, title: 'Research', status: 'done', agent_name: 'Agent One' }],
      }],
    });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });
    harness.apiGet.mockImplementation((path: string) => Promise.resolve({
      data: path === '/sessions/runs/run-1'
        ? [
            { id: 'step-session', step_index: 0 },
            { id: 'synthesis-session', step_index: null },
          ]
        : [],
    }));
    renderTaskDetail();

    const synthesis = await screen.findByRole('button', { name: /synthesis/i });
    const step = document.querySelector<HTMLButtonElement>('button[title^="Research"]');
    expect(step).not.toBeNull();
    expect(step).toHaveClass('min-h-11', 'min-w-11', 'md:min-h-0', 'md:min-w-0');
    expect(synthesis).toHaveClass('min-h-11', 'md:min-h-0');
  });

  it('shows live task text and tools before canonical chat is saved', async () => {
    harness.resources.set('/tasks/task-1', {
      data: { id: 'task-1', title: 'Task', status: 'in_progress' },
    });
    harness.resources.set('/tasks/task-1/runs', {
      data: [{
        id: 'run-live',
        status: 'running',
        plan: [{
          index: 0,
          title: 'Research',
          status: 'running',
          agent_name: 'Researcher',
        }],
      }],
    });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });

    let canonicalChat: unknown[] = [];
    harness.apiGet.mockImplementation((path: string) => {
      if (path === '/sessions/runs/run-live') {
        return Promise.resolve({
          data: [{ id: 'session-1', step_index: 0, step_title: 'Research' }],
        });
      }
      if (path === '/sessions/session-1/chat') {
        return Promise.resolve({ data: canonicalChat });
      }
      return Promise.resolve({ data: [] });
    });
    renderTaskDetail();
    await waitFor(() => expect(harness.taskRunOnEvent).not.toBeNull());

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-1',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 1,
      kind: 'text_delta',
      agent_name: 'Researcher',
      data: { turn_id: 'session-1:1', attempt: 1, content: 'working now' },
    }));
    expect(await screen.findByText('working now')).toBeInTheDocument();

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-2',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 2,
      kind: 'tool_started',
      agent_name: 'Researcher',
      data: {
        turn_id: 'session-1:1',
        tool_call_id: 'call-1',
        tool_name: 'read_file',
        params: '{"path":"README.md"}',
      },
    }));
    expect(await screen.findByText('running…')).toBeInTheDocument();

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-3',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 3,
      kind: 'tool_completed',
      agent_name: 'Researcher',
      data: {
        turn_id: 'session-1:1',
        tool_call_id: 'call-1',
        tool_name: 'read_file',
        result: 'file contents',
        status: 'done',
      },
    }));
    expect(await screen.findByText('file contents')).toBeInTheDocument();

    canonicalChat = [{
      role: 'assistant',
      type: 'text',
      name: 'Researcher',
      content: '# Finished',
    }];
    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-4',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 4,
      kind: 'turn_completed',
      agent_name: 'Researcher',
      data: { turn_id: 'session-1:1', attempt: 1 },
    }));

    expect(
      await screen.findByRole('heading', { name: 'Finished' }, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText('working now')).not.toBeInTheDocument();
  });

  it('keeps live output when canonical reconciliation fails', async () => {
    harness.resources.set('/tasks/task-1', {
      data: { id: 'task-1', title: 'Task', status: 'in_progress' },
    });
    harness.resources.set('/tasks/task-1/runs', {
      data: [{
        id: 'run-live',
        status: 'running',
        plan: [{
          index: 0,
          title: 'Research',
          status: 'running',
          agent_name: 'Researcher',
        }],
      }],
    });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });

    let failCanonical = false;
    harness.apiGet.mockImplementation((path: string) => {
      if (path === '/sessions/runs/run-live') {
        return Promise.resolve({
          data: [{ id: 'session-1', step_index: 0, step_title: 'Research' }],
        });
      }
      if (path === '/sessions/session-1/chat') {
        return failCanonical
          ? Promise.reject(new Error('offline'))
          : Promise.resolve({ data: [] });
      }
      return Promise.resolve({ data: [] });
    });
    renderTaskDetail();
    await waitFor(() => expect(harness.taskRunOnEvent).not.toBeNull());

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-1',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 1,
      kind: 'text_delta',
      agent_name: 'Researcher',
      data: {
        turn_id: 'session-1:1',
        attempt: 1,
        content: 'keep this partial output',
      },
    }));
    expect(
      await screen.findByText('keep this partial output'),
    ).toBeInTheDocument();

    failCanonical = true;
    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-2',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 2,
      kind: 'turn_completed',
      agent_name: 'Researcher',
      data: { turn_id: 'session-1:1', attempt: 1 },
    }));

    await waitFor(() => {
      const chatLoads = harness.apiGet.mock.calls.filter(
        ([path]) => path === '/sessions/session-1/chat',
      );
      expect(chatLoads.length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.getByText('keep this partial output')).toBeInTheDocument();
    expect(harness.pollingEnabled).toBe(true);
  });

  it('keeps newer canonical chat when an older load resolves last', async () => {
    harness.resources.set('/tasks/task-1', {
      data: { id: 'task-1', title: 'Task', status: 'in_progress' },
    });
    harness.resources.set('/tasks/task-1/runs', {
      data: [{
        id: 'run-live',
        status: 'running',
        plan: [{
          index: 0,
          title: 'Research',
          status: 'running',
          agent_name: 'Researcher',
        }],
      }],
    });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });

    type ChatResponse = { data: unknown[] };
    let resolveOlder!: (response: ChatResponse) => void;
    let resolveNewer!: (response: ChatResponse) => void;
    const olderResponse = new Promise<ChatResponse>((resolve) => {
      resolveOlder = resolve;
    });
    const newerResponse = new Promise<ChatResponse>((resolve) => {
      resolveNewer = resolve;
    });
    let chatLoads = 0;
    harness.apiGet.mockImplementation((path: string) => {
      if (path === '/sessions/runs/run-live') {
        return Promise.resolve({
          data: [{ id: 'session-1', step_index: 0, step_title: 'Research' }],
        });
      }
      if (path === '/sessions/session-1/chat') {
        chatLoads += 1;
        return chatLoads === 1 ? olderResponse : newerResponse;
      }
      return Promise.resolve({ data: [] });
    });
    renderTaskDetail();
    await waitFor(() => expect(chatLoads).toBe(1));

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-1',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 1,
      kind: 'text_delta',
      agent_name: 'Researcher',
      data: {
        turn_id: 'session-1:1',
        attempt: 1,
        content: 'newer partial output',
      },
    }));
    expect(await screen.findByText('newer partial output')).toBeInTheDocument();

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-2',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 2,
      kind: 'turn_completed',
      agent_name: 'Researcher',
      data: { turn_id: 'session-1:1', attempt: 1 },
    }));
    await waitFor(() => expect(chatLoads).toBe(2));

    await act(async () => {
      resolveNewer({
        data: [{
          role: 'assistant',
          type: 'text',
          name: 'Researcher',
          content: '# New canonical',
        }],
      });
      await newerResponse;
    });
    expect(
      await screen.findByRole('heading', { name: 'New canonical' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('newer partial output')).not.toBeInTheDocument();

    await act(async () => {
      resolveOlder({
        data: [{
          role: 'assistant',
          type: 'text',
          name: 'Researcher',
          content: '# Stale canonical',
        }],
      });
      await olderResponse;
    });
    expect(
      screen.getByRole('heading', { name: 'New canonical' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: 'Stale canonical' }),
    ).not.toBeInTheDocument();
  });

  it('reconciles a completed live turn through a superseding poll load', async () => {
    harness.resources.set('/tasks/task-1', {
      data: { id: 'task-1', title: 'Task', status: 'in_progress' },
    });
    harness.resources.set('/tasks/task-1/runs', {
      data: [{
        id: 'run-live',
        status: 'running',
        plan: [{
          index: 0,
          title: 'Research',
          status: 'running',
          agent_name: 'Researcher',
        }],
      }],
    });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });

    type ChatResponse = { data: unknown[] };
    let resolveCompletion!: (response: ChatResponse) => void;
    let resolvePoll!: (response: ChatResponse) => void;
    const completionResponse = new Promise<ChatResponse>((resolve) => {
      resolveCompletion = resolve;
    });
    const pollResponse = new Promise<ChatResponse>((resolve) => {
      resolvePoll = resolve;
    });
    let chatLoads = 0;
    harness.apiGet.mockImplementation((path: string) => {
      if (path === '/sessions/runs/run-live') {
        return Promise.resolve({
          data: [{ id: 'session-1', step_index: 0, step_title: 'Research' }],
        });
      }
      if (path === '/sessions/session-1/chat') {
        chatLoads += 1;
        if (chatLoads === 1) return Promise.resolve({ data: [] });
        return chatLoads === 2 ? completionResponse : pollResponse;
      }
      return Promise.resolve({ data: [] });
    });
    renderTaskDetail();
    await waitFor(() => expect(chatLoads).toBe(1));
    expect(harness.pollingIntervalMs).toBe(5000);

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-1',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 1,
      kind: 'text_delta',
      agent_name: 'Researcher',
      data: {
        turn_id: 'session-1:1',
        attempt: 1,
        content: 'completed partial output',
      },
    }));
    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-2',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 2,
      kind: 'text_delta',
      agent_name: 'Researcher',
      data: {
        turn_id: 'session-1:2',
        attempt: 1,
        content: 'unrelated live output',
      },
    }));
    expect(await screen.findByText('completed partial output')).toBeInTheDocument();
    expect(screen.getByText('unrelated live output')).toBeInTheDocument();

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-3',
      run_id: 'run-live',
      session_id: 'session-1',
      step_index: 0,
      sequence: 3,
      kind: 'turn_completed',
      agent_name: 'Researcher',
      data: { turn_id: 'session-1:1', attempt: 1 },
    }));
    await waitFor(() => expect(chatLoads).toBe(2));

    act(() => harness.pollingCallback?.());
    await waitFor(() => expect(chatLoads).toBe(3));

    await act(async () => {
      resolvePoll({
        data: [{
          role: 'assistant',
          type: 'text',
          name: 'Researcher',
          content: '# Poll canonical',
        }],
      });
      await pollResponse;
    });
    expect(
      await screen.findByRole('heading', { name: 'Poll canonical' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('completed partial output')).not.toBeInTheDocument();
    expect(screen.getByText('unrelated live output')).toBeInTheDocument();

    await act(async () => {
      resolveCompletion({
        data: [{
          role: 'assistant',
          type: 'text',
          name: 'Researcher',
          content: '# Superseded completion',
        }],
      });
      await completionResponse;
    });
    expect(
      screen.getByRole('heading', { name: 'Poll canonical' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: 'Superseded completion' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('completed partial output')).not.toBeInTheDocument();
    expect(screen.getByText('unrelated live output')).toBeInTheDocument();
  });

  it('rejects events from a run other than the selected run', async () => {
    harness.resources.set('/tasks/task-1', {
      data: { id: 'task-1', title: 'Task', status: 'failed' },
    });
    harness.resources.set('/tasks/task-1/runs', {
      data: [{
        id: 'run-selected',
        status: 'failed',
        plan: [{ index: 0, title: 'Research', status: 'failed' }],
      }],
    });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });
    harness.apiGet.mockImplementation((path: string) => Promise.resolve({
      data: path === '/sessions/runs/run-selected'
        ? [{ id: 'session-selected', step_index: 0 }]
        : [],
    }));
    renderTaskDetail();
    await waitFor(() => expect(harness.taskRunOnEvent).not.toBeNull());
    await waitFor(() => expect(harness.apiGet).toHaveBeenCalledWith(
      '/sessions/session-selected/chat',
    ));

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-other-run',
      run_id: 'run-other',
      session_id: 'session-selected',
      step_index: 0,
      sequence: 1,
      kind: 'text_delta',
      agent_name: 'Researcher',
      data: { turn_id: 'session-selected:1', content: 'wrong run output' },
    }));

    expect(screen.queryByText('wrong run output')).not.toBeInTheDocument();
  });

  it('invalidates pending chat loads and live bookkeeping when switching runs', async () => {
    harness.resources.set('/tasks/task-1', {
      data: { id: 'task-1', title: 'Task', status: 'failed' },
    });
    harness.resources.set('/tasks/task-1/runs', {
      data: [
        {
          id: 'run-a',
          status: 'failed',
          plan: [{ index: 0, title: 'Run A step', status: 'failed' }],
        },
        {
          id: 'run-b',
          status: 'failed',
          plan: [{ index: 0, title: 'Run B step', status: 'failed' }],
        },
      ],
    });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });

    type ChatResponse = { data: unknown[] };
    let resolveStaleRunA!: (response: ChatResponse) => void;
    const staleRunAResponse = new Promise<ChatResponse>((resolve) => {
      resolveStaleRunA = resolve;
    });
    let runAChatLoads = 0;
    harness.apiGet.mockImplementation((path: string) => {
      if (path === '/sessions/runs/run-a') {
        return Promise.resolve({ data: [{ id: 'session-a', step_index: 0 }] });
      }
      if (path === '/sessions/runs/run-b') {
        return Promise.resolve({ data: [{ id: 'session-b', step_index: 0 }] });
      }
      if (path === '/sessions/session-a/chat') {
        runAChatLoads += 1;
        if (runAChatLoads === 1) return Promise.resolve({ data: [] });
        return staleRunAResponse;
      }
      return Promise.resolve({ data: [] });
    });
    renderTaskDetail();
    await waitFor(() => expect(runAChatLoads).toBe(1));

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-a-1',
      run_id: 'run-a',
      session_id: 'session-a',
      step_index: 0,
      sequence: 1,
      kind: 'text_delta',
      agent_name: 'Researcher',
      data: { turn_id: 'session-a:1', content: 'run A live output' },
    }));
    expect(await screen.findByText('run A live output')).toBeInTheDocument();

    act(() => harness.taskRunOnEvent?.({
      type: 'task_run_event',
      id: 'event-a-2',
      run_id: 'run-a',
      session_id: 'session-a',
      step_index: 0,
      sequence: 2,
      kind: 'turn_completed',
      agent_name: 'Researcher',
      data: { turn_id: 'session-a:1' },
    }));
    await waitFor(() => expect(runAChatLoads).toBe(2));

    await userEvent.click(screen.getByRole('button', { name: /^#1\b/ }));
    await waitFor(() => expect(screen.queryByText('run A live output')).not.toBeInTheDocument());

    await act(async () => {
      resolveStaleRunA({
        data: [{
          role: 'assistant',
          type: 'text',
          content: '# Stale run A',
        }],
      });
      await staleRunAResponse;
    });

    await userEvent.click(screen.getByRole('button', { name: /^#2\b/ }));
    expect(screen.queryByRole('heading', { name: 'Stale run A' })).not.toBeInTheDocument();
    expect(screen.queryByText('run A live output')).not.toBeInTheDocument();
  });

  it('renders API key secrets with responsive input heights', async () => {
    harness.resources.set('/api-keys', { data: [] });
    harness.apiPost.mockResolvedValueOnce({
      data: { key: 'ck_test_secret', webhook_secret: 'whsec_test' },
    });
    renderRoute(<ApiKeys />);
    await userEvent.click(screen.getAllByRole('button', { name: '+ New key' })[0]);
    await userEvent.type(screen.getByRole('textbox', { name: /NAME/ }), 'CI key');
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }));

    expect(await screen.findByRole('textbox', { name: 'API KEY' })).toHaveClass('h-11', 'md:h-10');
    expect(screen.getByRole('textbox', { name: 'WEBHOOK SECRET' })).toHaveClass('h-11', 'md:h-10');
  });

  it('renders the AgentPage advanced disclosure with a responsive touch target', () => {
    harness.resources.set('/tools', { data: [] });
    renderAgentPage();

    expect(screen.getByRole('button', { name: /ADVANCED/ })).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
  });

  it('renders disconnected and retry actions as responsive touch targets', () => {
    harness.sseConnected = false;
    harness.sseError = new Error('offline');
    renderRoute(<Home />);

    expect(screen.getByRole('button', { name: 'reconnect' })).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
    expect(screen.getByRole('button', { name: /retry/i })).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
  });

  it('renders approval actions as responsive touch targets', () => {
    renderRoute(<Home />);

    act(() => {
      harness.sseOnMessage?.({
        type: 'approval_request',
        request_id: 'approval-1',
        tool_name: 'shell',
        risk_level: 'high',
        details: 'Run a command',
      });
    });

    const alert = screen.getByRole('alert');
    for (const name of ['approve', 'approve for session', 'deny']) {
      expect(within(alert).getByRole('button', { name })).toHaveClass(
        'min-h-11',
        'md:min-h-0',
      );
    }
  });

  it('renders attachment removal as a responsive square touch target', async () => {
    renderRoute(<Home />);
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).not.toBeNull();

    await userEvent.upload(fileInput!, new File(['notes'], 'note.txt', { type: 'text/plain' }));

    expect(await screen.findByRole('button', { name: 'Remove note.txt' })).toHaveClass(
      'h-11',
      'w-11',
      'md:h-auto',
      'md:w-auto',
    );
  });

  it('keeps PageForm saving text plain and announces it from a sibling live region', () => {
    renderRoute(
      <PageForm
        eyebrow="EDIT TASK"
        title="Task"
        backTo="/tasks"
        onSave={() => undefined}
        saving
      >
        <div>Fields</div>
      </PageForm>,
    );

    expectSiblingLiveStatus(screen.getByRole('button', { name: 'Saving…' }), 'Saving…');
  });

  it('keeps Login loading text plain and announces it from a sibling live region', async () => {
    harness.apiPost.mockReturnValueOnce(new Promise(() => undefined));
    renderRoute(<Login />);
    await userEvent.type(screen.getByRole('textbox', { name: 'EMAIL' }), 'test@example.com');
    await userEvent.type(screen.getByLabelText('PASSWORD'), 'password123');

    await userEvent.click(screen.getByRole('button', { name: /Sign in/ }));

    await waitFor(() =>
      expectSiblingLiveStatus(screen.getByRole('button', { name: 'Signing in…' }), 'Signing in…'),
    );
  });

  it('keeps Signup loading text plain and announces it from a sibling live region', async () => {
    harness.apiPost.mockReturnValueOnce(new Promise(() => undefined));
    renderRoute(<Signup />);
    await userEvent.type(screen.getByRole('textbox', { name: 'NAME' }), 'Ada');
    await userEvent.type(screen.getByRole('textbox', { name: 'EMAIL' }), 'ada@example.com');
    await userEvent.type(screen.getByLabelText('PASSWORD'), 'password123');
    await userEvent.type(screen.getByLabelText('CONFIRM'), 'password123');

    await userEvent.click(screen.getByRole('button', { name: /Create account/ }));

    await waitFor(() =>
      expectSiblingLiveStatus(screen.getByRole('button', { name: 'Creating…' }), 'Creating…'),
    );
  });

  it('keeps API key loading text plain and announces it from a sibling live region', async () => {
    harness.resources.set('/api-keys', { data: [] });
    harness.apiPost.mockReturnValueOnce(new Promise(() => undefined));
    renderRoute(<ApiKeys />);
    const newKeyActions = screen.getAllByRole('button', { name: '+ New key' });
    expect(newKeyActions).toHaveLength(2);
    await userEvent.click(newKeyActions[0]);
    await userEvent.type(screen.getByRole('textbox', { name: /NAME/ }), 'CI key');

    await userEvent.click(screen.getByRole('button', { name: 'Create key' }));

    await waitFor(() =>
      expectSiblingLiveStatus(screen.getByRole('button', { name: 'Creating…' }), 'Creating…'),
    );
  });

  it('keeps TaskDetail starting text plain and announces it from a sibling live region', async () => {
    harness.resources.set('/tasks/task-1', { data: { id: 'task-1', title: 'Task', status: 'pending' } });
    harness.resources.set('/tasks/task-1/runs', { data: [] });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });
    harness.apiGet.mockReturnValueOnce(new Promise(() => undefined));
    renderTaskDetail();

    await userEvent.click(screen.getByRole('button', { name: '▶ Run' }));

    await waitFor(() =>
      expectSiblingLiveStatus(screen.getByRole('button', { name: 'Starting…' }), 'Starting…'),
    );
  });

  it('keeps TaskDetail cancelling text plain and announces it from a sibling live region', async () => {
    harness.resources.set('/tasks/task-1', {
      data: { id: 'task-1', title: 'Task', status: 'in_progress' },
    });
    harness.resources.set('/tasks/task-1/runs', {
      data: [{ id: 'run-1', status: 'cancelling', plan: [] }],
    });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });
    renderTaskDetail();

    const button = await screen.findByRole('button', { name: 'Cancelling… (click to force)' });
    expectSiblingLiveStatus(button, 'Cancelling… (click to force)');
  });
});
