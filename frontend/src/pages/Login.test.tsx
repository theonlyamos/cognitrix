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
vi.mock('@/components/OAuthButtons', () => ({ default: () => null }));
vi.mock('@/components/ThemeToggle', () => ({ ThemeToggle: () => null }));

beforeEach(() => {
  [get, post, login].forEach((fn) => fn.mockReset());
  get.mockResolvedValue({ data: { signup_open: false, password_reset_by_email: false, code_sign_in: true } });
  window.history.replaceState(null, '', '/');
});

const show = () => render(<MemoryRouter><Login /></MemoryRouter>);

describe('Login with a sign-in code', () => {
  it('signs in with a typed code', async () => {
    post.mockResolvedValue({ data: { user: { name: 'Ada', email: 'ada@example.com' }, access_token: 'tok' } });
    show();
    fireEvent.click(await screen.findByRole('tab', { name: 'Sign-in code' }));
    const input = screen.getByLabelText('CODE FROM A SIGNED-IN DEVICE');
    fireEvent.change(input, { target: { value: 'kxrt4m9p' } });
    expect(input).toHaveValue('KXRT-4M9P');
    fireEvent.click(screen.getByRole('button', { name: /Sign in/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/auth/code-sign-in', { code: 'KXRT-4M9P' }));
    expect(login).toHaveBeenCalledWith({ name: 'Ada', email: 'ada@example.com' }, 'tok');
  });

  it('fills in the code from a scanned QR link and takes it out of the address', async () => {
    window.history.replaceState(null, '', '/#signin-code=KXRT4M9P');
    show();
    expect(await screen.findByLabelText('CODE FROM A SIGNED-IN DEVICE')).toHaveValue('KXRT-4M9P');
    expect(window.location.hash).toBe('');
    expect(post).not.toHaveBeenCalled();
  });

  it('shows why a code failed', async () => {
    post.mockRejectedValue(Object.assign(new Error('bad'), {
      isAxiosError: true, response: { status: 400, data: { detail: "That code isn't right or has expired." } },
    }));
    show();
    fireEvent.click(await screen.findByRole('tab', { name: 'Sign-in code' }));
    fireEvent.change(screen.getByLabelText('CODE FROM A SIGNED-IN DEVICE'), { target: { value: 'AAAABBBB' } });
    fireEvent.click(screen.getByRole('button', { name: /Sign in/ }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it('has no code tab when the server turned it off', async () => {
    get.mockResolvedValue({ data: { signup_open: false, password_reset_by_email: false, code_sign_in: false } });
    show();
    await waitFor(() => expect(get).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('tab', { name: 'Sign-in code' })).not.toBeInTheDocument());
  });
});
