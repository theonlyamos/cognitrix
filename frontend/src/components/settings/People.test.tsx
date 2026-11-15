import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PeopleSettings } from './People';

const { get, post, patch, put, del } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), del: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, post, patch, put, delete: del },
}));

const person = (over = {}) => ({
  id: 'u1', name: 'Ada', email: 'ada@example.com', role: 'admin', disabled: false, you: true, created: '2026-11-01',
  usage_30d: { calls: 2, tokens: 1500, cost: 0.5 }, ...over,
});

beforeEach(() => {
  [get, post, patch, put, del].forEach((fn) => fn.mockReset());
  get.mockImplementation((url: string) => Promise.resolve({
    data: url === '/admin/users'
      ? { users: [person(), person({ id: 'u2', name: 'Bob', email: 'bob@example.com', role: 'member', you: false })], email_configured: false }
      : [],
  }));
});

describe('PeopleSettings', () => {
  it('lists people with usage and keeps your own role and access out of reach', async () => {
    render(<PeopleSettings />);
    expect(await screen.findByText('Ada')).toBeInTheDocument();
    expect(screen.getAllByText(/1\.5k tokens, \$0\.50/).length).toBe(2);
    // Only Bob can be disabled or promoted.
    expect(screen.getAllByRole('button', { name: 'Disable' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Make admin' })).toHaveLength(1);
    expect(screen.getByText(/No email service is set up/)).toBeInTheDocument();
  });

  it('disables a person', async () => {
    patch.mockResolvedValue({ data: person({ id: 'u2', name: 'Bob', role: 'member', you: false, disabled: true }) });
    render(<PeopleSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Disable' }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith('/admin/users/u2', { disabled: true }));
    expect(await screen.findByRole('button', { name: 'Enable' })).toBeInTheDocument();
  });

  it('creates an invitation and shows its link once', async () => {
    post.mockResolvedValue({ data: { id: 'i1', path: '/signup?invite=abc' } });
    render(<PeopleSettings />);
    await screen.findByText('Ada');
    fireEvent.change(screen.getByLabelText('Their email (optional)'), { target: { value: 'new@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create link' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/admin/invites', { email: 'new@example.com', role: 'member' }));
    expect(await screen.findByText(/\/signup\?invite=abc$/)).toBeInTheDocument();
  });

  it('sets a spending limit for one person and shows how much is used', async () => {
    const spending = {
      mode: 'custom', own_cost: 20, own_tokens: 0,
      cap: { source: 'own', mode: 'custom', monthly_cost: 20, monthly_tokens: 0 },
      used: { cost: 17, tokens: 900000, unpriced_tokens: 0, calls: 40 },
      fraction: 0.85, reached: false, month: '2026-11', resets_on: '2026-12-01',
    };
    put.mockResolvedValue({ data: person({ id: 'u2', name: 'Bob', email: 'bob@example.com', role: 'member', you: false, spending }) });
    render(<PeopleSettings />);
    await screen.findByText('Bob');
    fireEvent.click(screen.getAllByRole('button', { name: 'Spending limit' })[1]);
    const form = screen.getByRole('form', { name: 'Spending limit for Bob' });
    fireEvent.change(form.querySelector('select')!, { target: { value: 'custom' } });
    fireEvent.change(screen.getByLabelText('Dollars a month', { selector: 'form[aria-label="Spending limit for Bob"] input' }), { target: { value: '20' } });
    fireEvent.click(form.querySelector('button[type="submit"]')!);
    await waitFor(() => expect(put).toHaveBeenCalledWith('/admin/users/u2/spending-cap', { mode: 'custom', monthly_cost: 20, monthly_tokens: 0 }));
    expect(await screen.findByText('This month: $17.00 of $20.00')).toBeInTheDocument();
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '85');
  });

  it('removes a person only after their email is typed', async () => {
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/admin/users'
        ? { users: [person(), person({ id: 'u2', name: 'Bob', email: 'bob@example.com', role: 'member', you: false })], email_configured: false }
        : url === '/admin/users/u2/removal' ? { agents: 2, tasks: 1, teams: 0, apps: 0, chats: 5 } : [],
    }));
    post.mockResolvedValue({ data: { message: 'bob@example.com was removed' } });
    render(<PeopleSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    const dialog = await screen.findByRole('dialog', { name: 'Remove Bob?' });
    expect(await screen.findByText(/2 agents, 1 task/)).toBeInTheDocument();
    const confirm = screen.getAllByRole('button', { name: 'Remove' }).find((b) => dialog.contains(b))!;
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByLabelText('Delete them too'));
    fireEvent.change(screen.getByLabelText('Their email address'), { target: { value: 'Bob@example.com' } });
    fireEvent.click(confirm);
    await waitFor(() => expect(post).toHaveBeenCalledWith('/admin/users/u2/remove', { keep_work: false, confirm_email: 'Bob@example.com' }));
    expect(await screen.findByText('bob@example.com was removed')).toBeInTheDocument();
  });
});
