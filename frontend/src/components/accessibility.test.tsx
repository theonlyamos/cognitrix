import type { ComponentType, ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CheckList, Field } from '@/components/form';
import Sidebar from '@/components/Sidebar';
import Home from '@/pages/Home';
import { ThemeProvider } from '@/context/ThemeContext';
import { Input } from '@/lib/components/ui/input';

const session = vi.hoisted(() => ({
  messages: [
    {
      id: 'tools',
      role: 'tool' as const,
      content: '',
      tools: [
        { id: 'done', name: 'read_file', status: 'done' as const, result: 'ok' },
        { id: 'failed', name: 'write_file', status: 'error' as const, result: 'denied' },
      ],
    },
  ],
}));

vi.mock('@/context/AppContext', () => ({
  useUser: () => ({
    user: { id: 'user-1', name: 'Test User', email: 'test@example.com' },
    logout: vi.fn(),
  }),
}));

vi.mock('@/context/SessionContext', () => ({
  useSession: () => ({
    messages: session.messages,
    addMessage: vi.fn(),
    appendToLastMessage: vi.fn(),
    setIsStreaming: vi.fn(),
    addToolCall: vi.fn(),
    resolveToolCall: vi.fn(),
    clearMessages: vi.fn(),
    setMessages: vi.fn(),
  }),
}));

vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path === '/agents' ? [{ id: 'agent-1', name: 'Agent One' }] : [],
    loading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock('@/hooks/useSSE', () => ({
  useSSE: () => ({ isConnected: true, error: null, reconnect: vi.fn() }),
}));

type SelectionRowProps = {
  selected: boolean;
  onSelect: () => void;
  trailingAction?: ReactNode;
  children: ReactNode;
};

const selectionRowModule = import.meta.glob('./SelectionRow.tsx', { eager: true }) as Record<
  string,
  { SelectionRow?: ComponentType<SelectionRowProps> }
>;

const options = [{ value: 'read', label: 'Read' }];

function renderSidebar(path: string) {
  return render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
    >
      <ThemeProvider>
        <Sidebar />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

function renderToolRows() {
  return render(
    <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Home />
    </MemoryRouter>,
  );
}

describe('interface accessibility', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('selectedAgentId', 'agent-1');
    localStorage.setItem('chatSession:agent-1', '');
    Element.prototype.scrollIntoView = vi.fn();
  });

  it('names simple controls and composite checkbox groups', () => {
    render(
      <>
        <Field label="NAME"><Input /></Field>
        <Field label="SCOPES" composite>
          <CheckList options={options} selected={new Set()} onToggle={() => {}} />
        </Field>
      </>,
    );

    expect(screen.getByRole('textbox', { name: 'NAME' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'SCOPES' })).toBeInTheDocument();
  });

  it('uses a keyboard-operable pressed button for selectable rows', async () => {
    const SelectionRow = selectionRowModule['./SelectionRow.tsx']?.SelectionRow;
    expect(SelectionRow, 'SelectionRow component is missing').toBeTypeOf('function');
    if (!SelectionRow) return;

    const onSelect = vi.fn();
    render(<SelectionRow selected onSelect={onSelect}>Run 1</SelectionRow>);
    const row = screen.getByRole('button', { name: 'Run 1' });

    expect(row).toHaveAttribute('aria-pressed', 'true');
    row.focus();
    await userEvent.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it('marks the active navigation destination and omits dead search UI', () => {
    renderSidebar('/tasks');

    expect(screen.getByRole('link', { name: 'Tasks' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('button', { name: /Search & run/i })).not.toBeInTheDocument();
  });

  it('exposes completed and failed tool states as text', () => {
    renderToolRows();

    expect(screen.getByText('Completed')).toHaveClass('sr-only');
    expect(screen.getByText('Failed')).toHaveClass('sr-only');
  });
});
