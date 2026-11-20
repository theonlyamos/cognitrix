import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Projects from './Projects';
import ProjectDetail from './ProjectDetail';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api,
}));

const project = {
  id: 'p1', name: 'Shop website', description: 'The new storefront', instructions: '', color: 'green',
  agents: ['a1'], folders: [], knowledge: [], computer: 'sandbox', pc_folders: [], use_account_memory: true,
  pinned: false, archived: false, created: '2026-11-20 10:00:00', updated: '2026-11-20 10:00:00',
};

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  api.get.mockImplementation(async (path: string) => {
    if (path === '/projects') return { data: { projects: [{ ...project, chats: 2, last_active: '' }] } };
    if (path === '/projects/p1') return { data: { ...project, chats: [{ id: 's1', title: 'Fix the cart', agent_id: 'a1', team_id: null, kind: 'agent', pinned: false, archived: false, message_count: 4, updated_at: '' }], tasks: [] } };
    if (path === '/projects/p1/changes') {
      return { data: { files: 1, added: 3, removed: 1, pending: 1, chats: [{ session_id: 's1', title: 'Fix the cart', agent_id: 'a1', updated_at: '', files: 1, added: 3, removed: 1, pending: 1, sources: [] }] } };
    }
    if (path === '/sessions/s1/changes') return { data: { sources: [] } };
    if (path === '/agents') return { data: [{ id: 'a1', name: 'Coder' }] };
    if (path === '/computer/devices') return { data: { devices: [] } };
    return { data: [] };
  });
});

describe('Projects', () => {
  it('lists projects as cards with their chats, computer and agents', async () => {
    render(<MemoryRouter><Projects /></MemoryRouter>);
    expect(await screen.findByText('Shop website')).toBeInTheDocument();
    expect(screen.getByText('2 chats')).toBeInTheDocument();
    expect(screen.getByText('Shared sandbox')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Coder')).toBeInTheDocument());
  });

  it('creates a project from the dialog', async () => {
    api.post.mockResolvedValue({ data: { ...project, id: 'p2', name: 'Docs' } });
    render(<MemoryRouter><Projects /></MemoryRouter>);
    await screen.findByText('Shop website');
    fireEvent.click(screen.getAllByRole('button', { name: 'New project' })[0]);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Docs' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects', expect.objectContaining({ name: 'Docs', computer: 'sandbox' })));
  });

  it('shows the project page with its chats and a Changes tab', async () => {
    render(
      <MemoryRouter initialEntries={['/projects/p1/changes']}>
        <Routes><Route path="/projects/:projectId/:tab" element={<ProjectDetail />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { name: /Shop website/ })).toBeInTheDocument();
    expect(await screen.findByText('Fix the cart')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Changes/ })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/sessions/s1/changes'));
  });
});
