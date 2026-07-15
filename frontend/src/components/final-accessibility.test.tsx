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

vi.mock('@/hooks/usePolling', () => ({ usePolling: () => undefined }));

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

  it('keeps the Home header on one compact mobile row', () => {
    renderRoute(<Home />);

    expect(screen.getByRole('banner')).toHaveClass('flex-row', 'flex-nowrap');
    expect(screen.getByRole('banner')).not.toHaveClass('overflow-hidden');
    expect(screen.getByRole('heading', { name: 'Chat' })).toHaveClass('sr-only', 'md:not-sr-only');
    expect(screen.getByRole('combobox', { name: 'Active agent' })).toHaveClass('w-full', 'min-w-0');
    expect(screen.getByRole('button', { name: 'Open conversations' })).toHaveClass('h-11', 'w-11', 'md:hidden');
    const connectionStatus = screen.getByLabelText('Connection status: connected');
    expect(connectionStatus).toHaveClass('flex', 'items-center');
    expect(within(connectionStatus).getByText('connected')).toHaveClass('sr-only', 'md:not-sr-only');
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

  it('keeps the pending TaskDetail header on one compact mobile row', () => {
    harness.resources.set('/tasks/task-1', { data: { id: 'task-1', title: 'Task', status: 'pending' } });
    harness.resources.set('/tasks/task-1/runs', { data: [] });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });
    renderTaskDetail();

    expect(screen.getByRole('banner')).toHaveClass('flex-row', 'flex-nowrap');
    expect(screen.getByRole('banner')).not.toHaveClass('overflow-hidden');
    expect(screen.getByRole('button', { name: 'Open runs' })).toHaveClass('h-11', 'w-11', 'md:hidden');
    expect(screen.getByRole('button', { name: 'Run task' })).toHaveClass('h-11', 'w-11');
    expect(screen.getByRole('button', { name: 'More actions' })).toBeInTheDocument();
  });

  it('keeps Resume visible and moves fresh runs into TaskDetail page actions', async () => {
    const user = userEvent.setup();
    harness.resources.set('/tasks/task-1', { data: { id: 'task-1', title: 'Task', status: 'failed' } });
    harness.resources.set('/tasks/task-1/runs', { data: [{ id: 'run-1', status: 'failed', plan: [] }] });
    harness.resources.set('/sessions/tasks/task-1', { data: [] });
    renderTaskDetail();

    expect(await screen.findByRole('button', { name: 'Resume task' })).toHaveClass('h-11', 'w-11');
    await user.click(screen.getByRole('button', { name: 'More actions' }));
    expect(screen.getByRole('dialog', { name: 'Page actions' })).toHaveTextContent('Run from beginning');
    expect(screen.getByRole('dialog', { name: 'Page actions' })).toHaveTextContent('Edit task');
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

    expect(screen.getByRole('button', { name: 'Reconnect' })).toHaveClass(
      'h-11',
      'w-11',
      'md:h-8',
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

  it('keeps PageForm actions on one mobile row with a compact save and contextual actions', () => {
    renderRoute(
      <PageForm
        eyebrow="EDIT TASK"
        title="A very long task title that must truncate in the mobile header"
        backTo="/tasks"
        onSave={() => undefined}
        onDelete={() => undefined}
      >
        <div>Fields</div>
      </PageForm>,
    );

    expect(screen.getByRole('banner')).toHaveClass('flex-row', 'flex-nowrap');
    expect(screen.getByRole('banner')).not.toHaveClass('overflow-hidden');
    expect(screen.getByRole('heading', { name: /very long task title/i })).toHaveClass('truncate');
    expect(screen.getByRole('button', { name: 'Save changes' })).toHaveClass('h-11', 'w-11');
    expect(screen.getByRole('button', { name: 'More actions' })).toBeInTheDocument();
  });

  it('omits redundant PageForm overflow actions when the back control is the only secondary action', () => {
    renderRoute(
      <PageForm
        eyebrow="NEW TEAM"
        title="New team"
        backTo="/teams"
        onSave={() => undefined}
      >
        <div>Fields</div>
      </PageForm>,
    );

    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
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
