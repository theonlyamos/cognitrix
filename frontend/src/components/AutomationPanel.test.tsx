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
