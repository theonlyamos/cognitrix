import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { AutomationPanel } from './AutomationPanel';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api,
}));

const hook = { id: 't1', name: 'Releases', kind: 'webhook', enabled: true, token_hint: 'abcd', last_fired_at: null, fire_count: 2 };
const mail = {
  id: 't2', name: 'New mail in bob@gmail.com', kind: 'gmail', enabled: true, token_hint: '', last_fired_at: null, fire_count: 0,
  match: 'invoice', checked_at: null, error: 'Gmail needs reconnecting in Settings › Connected apps.',
};

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  api.get.mockImplementation(async (url: string) => ({ data: url.endsWith('/triggers') ? [hook, mail] : [] }));
  api.post.mockResolvedValue({ data: mail });
});

describe('AutomationPanel triggers', () => {
  it('shows a new-mail trigger with its filter and problem, and no URL to rotate', async () => {
    render(<AutomationPanel taskId="task-1" task={{}} onChanged={() => {}} />);
    const row = (await screen.findByText('New mail in bob@gmail.com')).closest('li')!;
    expect(row).toHaveTextContent('only mail matching "invoice"');
    expect(within(row).getByRole('alert')).toHaveTextContent('Gmail needs reconnecting');
    expect(within(row).queryByRole('button', { name: 'New URL' })).not.toBeInTheDocument();
    const webhook = screen.getByText('Releases').closest('li')!;
    expect(within(webhook).getByRole('button', { name: 'New URL' })).toBeInTheDocument();
  });

  it('adds a new-mail trigger with an optional filter', async () => {
    render(<AutomationPanel taskId="task-1" task={{}} onChanged={() => {}} />);
    await screen.findByText('Releases');
    fireEvent.change(screen.getByLabelText('Only mail whose sender or subject contains'), { target: { value: ' invoice ' } });
    fireEvent.click(screen.getByRole('button', { name: '+ Start on new Gmail' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/tasks/task-1/triggers', { kind: 'gmail', match: 'invoice' }));
    // No secret URL to show for mail.
    expect(screen.queryByText(/Copy this URL now/)).not.toBeInTheDocument();
  });
});

describe('AutomationPanel results', () => {
  const made = { agent_id: 'a1', agent_name: 'Scout', kind: 'chat' as const, session_id: 'chat-9' };

  it('is hidden for tasks people made', async () => {
    render(<AutomationPanel taskId="task-1" task={{}} onChanged={() => {}} />);
    await screen.findByText('Releases');
    expect(screen.queryByLabelText('Results go to')).not.toBeInTheDocument();
  });

  it('shows who made the task and saves who gets its results', async () => {
    api.put.mockResolvedValue({ data: {} });
    const onChanged = vi.fn();
    render(<AutomationPanel taskId="task-1" task={{ created_by: made, report_to_effective: 'creator' }} onChanged={onChanged} />);
    expect(screen.getByText(/Scout made this task in a chat/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the chat' })).toHaveAttribute('href', '/home?chat=chat-9');
    expect(screen.getByText(/decides whether to tell you/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Tell the agent'), { target: { value: 'changed' } });
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/tasks/task-1/reporting', { report_to: null, report_only_on: 'changed' }));
    fireEvent.change(screen.getByLabelText('Results go to'), { target: { value: 'both' } });
    await waitFor(() => expect(api.put).toHaveBeenLastCalledWith('/tasks/task-1/reporting', { report_to: 'both', report_only_on: null }));
    expect(onChanged).toHaveBeenCalled();
  });
});
