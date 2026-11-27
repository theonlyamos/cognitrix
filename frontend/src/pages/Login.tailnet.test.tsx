import type { ReactNode } from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Login from './Login';

const { get, post, login } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), login: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, post },
}));
vi.mock('@/context/AppContext', () => ({ useUser: () => ({ login }) }));
vi.mock('@/components/OAuthButtons', () => ({ default: ({ first }: { first?: ReactNode }) => <div>{first}</div> }));
vi.mock('@/components/ThemeToggle', () => ({ ThemeToggle: () => null }));

beforeEach(() => { [get, post, login].forEach((fn) => fn.mockReset()); });

describe('Login from the tailnet', () => {
  it('offers the account linked to this Tailscale login and signs in with one click', async () => {
    get.mockResolvedValue({ data: { signup_open: false, code_sign_in: true, tailscale_login: 'ada@example.com' } });
    post.mockResolvedValue({ data: { user: { name: 'Ada', email: 'ada@example.com' }, access_token: 'tok' } });
    render(<MemoryRouter><Login /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Continue as ada@example.com' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/tailscale/sign-in', {}));
    expect(login).toHaveBeenCalledWith({ name: 'Ada', email: 'ada@example.com' }, 'tok');
  });

  it('has no such button anywhere else', async () => {
    get.mockResolvedValue({ data: { signup_open: false, code_sign_in: true, tailscale_login: '' } });
    render(<MemoryRouter><Login /></MemoryRouter>);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /Continue as/ })).toBeNull();
  });
});
