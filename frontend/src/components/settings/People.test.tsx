import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PeopleSettings } from './People';

const { get, post, patch, del } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, post, patch, delete: del },
}));

const person = (over = {}) => ({
  id: 'u1', name: 'Ada', email: 'ada@example.com', role: 'admin', disabled: false, you: true, created: '2026-11-01',
  usage_30d: { calls: 2, tokens: 1500, cost: 0.5 }, ...over,
});

beforeEach(() => {
  [get, post, patch, del].forEach((fn) => fn.mockReset());
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
});
