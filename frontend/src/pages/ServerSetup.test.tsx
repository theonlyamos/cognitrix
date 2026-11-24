import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ServerSetup from './ServerSetup';
import Login from './Login';

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, post },
}));
vi.mock('@/context/AppContext', () => ({ useUser: () => ({ login: vi.fn() }) }));
vi.mock('@/components/OAuthButtons', () => ({ default: () => null }));
vi.mock('@/components/ThemeToggle', () => ({ ThemeToggle: () => null }));

const SETUP = {
  database: { type: 'sqlite', host: '', port: '', name: '', user: '', password_set: false },
  queue: { redis: false },
  sqlite_path: '/home/cognitrix/.cognitrix/cognitrix.db',
};

beforeEach(() => {
  [get, post].forEach((fn) => fn.mockReset());
  get.mockImplementation((url: string) => Promise.resolve({
    data: url === '/setup/server' ? SETUP : { signup_open: true, server_setup: true },
  }));
});

const show = (path: string) => render(
  <MemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path="/" element={<Login />} />
      <Route path="/setup" element={<ServerSetup />} />
      <Route path="/signup" element={<p>sign up page</p>} />
    </Routes>
  </MemoryRouter>,
);

describe('Server setup on a new server', () => {
  it('opens from the sign-in page and keeps the built-in choices', async () => {
    post.mockResolvedValue({ data: { restart_needed: false } });
    show('/');
    expect(await screen.findByText('Set up this server')).toBeInTheDocument();
    expect(await screen.findByText(/cognitrix\.db/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/setup/server', expect.objectContaining({ db_type: 'sqlite', queue: 'builtin' })));
    expect(await screen.findByText('sign up page')).toBeInTheDocument();
  });

  it('asks for a restart after switching to a database server', async () => {
    post.mockResolvedValue({ data: { restart_needed: true } });
    show('/setup');
    fireEvent.change(await screen.findByLabelText('Keep data in'), { target: { value: 'postgresql' } });
    fireEvent.change(screen.getByLabelText('Server address'), { target: { value: 'db.example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/setup/server', expect.objectContaining({
      db_type: 'postgresql', db_host: 'db.example.com', db_password: 'pw',
    })));
    expect(await screen.findByText(/Restart the server/)).toBeInTheDocument();
  });
});
