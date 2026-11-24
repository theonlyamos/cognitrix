import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Board from './Board';
import type { BoardPost } from '@/lib/board';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api,
}));

function post(over: Partial<BoardPost>): BoardPost {
  return {
    id: 'p1', board: 'agents', kind: 'finding', text: 'Acme raised prices.', tags: [], author_kind: 'agent',
    author_id: 'a1', author_name: 'Researcher', chat_id: 'c1', to_name: '', to_id: '', status: '', pinned: false,
    replies: [], wake: {}, posted_at: '2026-11-23T10:00:00.000Z', changed_at: '2026-11-23T10:00:00.000Z', ...over,
  };
}

const posts = [
  post({ id: 'p1', kind: 'decision', text: 'Prices are ex-VAT.', pinned: true }),
  post({ id: 'p2', kind: 'question', text: 'Which currency?', status: 'open', to_name: 'you' }),
  post({ id: 'p3', kind: 'finding', text: 'Acme raised prices.', tags: ['acme'] }),
];

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  api.get.mockImplementation((path: string) => Promise.resolve({
    data: path.startsWith('/board/posts') ? posts
      : path === '/board/boards' ? [
        { key: 'agents', kind: 'agents', title: 'All agents', status: '', open: 1, total: 3, latest: '' },
        { key: 'swarm:s1', kind: 'swarm', title: 'Pricing scan', status: 'running', open: 0, total: 0, latest: '', swarm_id: 's1' },
      ]
      : path === '/board/follows' ? [{ agent_id: 'a1', agent_name: 'Researcher', kinds: ['decision'], tags: [], wake: true }]
      : path.startsWith('/board/mentions') ? [{ name: 'Researcher' }, { name: 'Research Lead' }, { name: 'Writer' }]
      : {},
  }));
  api.post.mockResolvedValue({ data: {} });
  api.put.mockResolvedValue({ data: {} });
  api.patch.mockResolvedValue({ data: {} });
});

describe('Board', () => {
  it('lists boards and posts, pinned first, and filters by kind', async () => {
    render(<MemoryRouter><Board /></MemoryRouter>);
    expect(await screen.findByText('Prices are ex-VAT.')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Pricing scan/ }).length).toBeGreaterThan(0);
    const cards = screen.getAllByRole('article');
    expect(cards[0]).toHaveTextContent('Prices are ex-VAT.');
    expect(within(cards[1]).getByText('Open · for you')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Finding/ }));
    expect(screen.queryByText('Which currency?')).not.toBeInTheDocument();
    expect(screen.getByText('Acme raised prices.')).toBeInTheDocument();
  });

  it('answers a question and posts as the person', async () => {
    render(<MemoryRouter><Board /></MemoryRouter>);
    const card = (await screen.findByText('Which currency?')).closest('article')!;
    fireEvent.click(within(card).getByRole('button', { name: 'Answer' }));
    fireEvent.change(within(card).getByLabelText('Your answer'), { target: { value: 'Cedis.' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Answer and close' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/board/posts/p2/reply', { text: 'Cedis.', close: true }));

    fireEvent.change(screen.getByLabelText('New post'), { target: { value: 'Launch moved to March.' } });
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'decision' } });
    fireEvent.change(screen.getByLabelText('For'), { target: { value: 'Researcher' } });
    fireEvent.click(screen.getByRole('button', { name: 'Post' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/board/posts', {
      board: 'agents', kind: 'decision', text: 'Launch moved to March.', to: 'Researcher', tags: '', thread: '',
    }));
  });

  it('suggests agents after @, shows tags, and edits or withdraws your own post', async () => {
    posts.push(post({ id: 'p4', author_kind: 'person', author_name: 'Ama', author_id: '', mine: true, text: 'Ask @Writer first.',
      mentions: [{ name: 'Writer' }] }));
    render(<MemoryRouter><Board /></MemoryRouter>);
    const mine = (await screen.findByText(/Ask/)).closest('article')!;
    expect(within(mine).getByText('@Writer')).toHaveClass('font-medium');

    const box = screen.getByLabelText('New post');
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/board/mentions')));
    fireEvent.change(box, { target: { value: 'Hi @res', selectionStart: 7 } });
    const list = await screen.findByRole('listbox');
    expect(within(list).getAllByRole('option').map((o) => o.textContent)).toEqual(['@Researcher', '@Research Lead']);
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(box).toHaveValue('Hi @Research Lead ');

    fireEvent.click(within(mine).getByRole('button', { name: 'Edit' }));
    fireEvent.change(within(mine).getByLabelText('Edit post'), { target: { value: 'Ask @Writer.' } });
    fireEvent.click(within(mine).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/board/posts/p4', { text: 'Ask @Writer.' }));
    fireEvent.click(within(mine).getByRole('button', { name: 'Withdraw' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/board/posts/p4/retract', {}));
    posts.pop();
  });

  it('searches the board', async () => {
    render(<MemoryRouter><Board /></MemoryRouter>);
    await screen.findByText('Prices are ex-VAT.');
    fireEvent.change(screen.getByLabelText('Search the board'), { target: { value: 'vat' } });
    await waitFor(() => expect(api.get.mock.calls.map((c) => c[0])).toContain('/board/posts?board=agents&q=vat'));
  });

  it('saves a decision to memory', async () => {
    render(<MemoryRouter><Board /></MemoryRouter>);
    const card = (await screen.findByText('Prices are ex-VAT.')).closest('article')!;
    fireEvent.click(within(card).getByRole('button', { name: 'Save to memory' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/board/posts/p1/remember', {}));
  });

  it('pages through older posts', async () => {
    const many = Array.from({ length: 50 }, (_, i) => post({ id: `n${i}`, text: `post ${i}`, stamp: 1000 - i }));
    api.get.mockImplementation((path: string) => Promise.resolve({
      data: path.includes('before=951') ? [post({ id: 'old1', text: 'An old post', stamp: 10 })]
        : path.startsWith('/board/posts') ? many : path === '/board/boards' ? [] : [],
    }));
    render(<MemoryRouter><Board /></MemoryRouter>);
    await screen.findByText('post 0');
    fireEvent.click(screen.getByRole('button', { name: 'Show older posts' }));
    expect(await screen.findByText('An old post')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show older posts' })).not.toBeInTheDocument();
  });

  it('shows a project channel with threads, claims, repeating posts and commands', async () => {
    const channelPosts = [
      post({ id: 'c1', board: 'project:pr1', kind: 'request', text: 'Write the launch post.', status: 'open',
        thread: 'task:t1', thread_title: 'Launch post', claimed_by: { kind: 'agent', name: 'Writer' } }),
      post({ id: 'c2', board: 'project:pr1', kind: 'request', text: 'Check the prices.', status: 'open',
        thread: 'task:t2', thread_title: 'Prices', people: [{ name: 'Kofi' }] }),
    ];
    api.get.mockImplementation((path: string) => Promise.resolve({
      data: path.startsWith('/board/posts') ? channelPosts
        : path === '/board/boards' ? [
          { key: 'agents', kind: 'agents', title: 'All agents', status: '', open: 0, total: 0, latest: '' },
          { key: 'project:pr1', kind: 'project', title: 'Launch', status: '', open: 2, total: 2, latest: '', project_id: 'pr1' },
        ]
        : path.startsWith('/board/channels') ? {
          board: 'project:pr1', project_id: 'pr1', title: 'Launch', owner: true,
          members: [{ id: 'u2', name: 'Kofi', email: 'kofi@example.com' }],
          project_agents: [{ id: 'a1', name: 'Researcher' }], agents: [], externals: [], known_externals: ['Codex'],
        }
        : path.startsWith('/board/schedules') ? []
        : path === '/board/commands' ? { commands: [], suggested: [{ name: 'review', description: 'Review a change', prompt: 'Review it.' }] }
        : path.startsWith('/board/mirrors') ? { slack_connected: false, mirrors: [] }
        : path === '/board/follows' ? [{ agent_id: 'a1', agent_name: 'Researcher', kinds: [], tags: [], wake: true },
          { agent_id: 'a2', agent_name: 'Writer', kinds: [], tags: [], wake: true }]
        : path.startsWith('/board/mentions') ? [{ name: 'Researcher', kind: 'agent' }, { name: 'Kofi', kind: 'person' }]
        : {},
    }));
    api.post.mockImplementation((path: string) => Promise.resolve({ data: path === '/board/schedules'
      ? { id: 's1', board: 'project:pr1', thread: '', to: 'Researcher', kind: 'request', text: 'Check PRs', days: [0, 1, 2, 3, 4],
        time: '09:00', when: 'weekdays at 09:00', enabled: true, next_at: '', last_at: '', created_by: 'Ama' } : {} }));
    api.put.mockImplementation((path: string) => Promise.resolve({ data: path.startsWith('/board/commands')
      ? { name: 'review', agent_id: 'a1', agent_name: 'Researcher', prompt: 'Review it.', description: 'Review a change' } : {} }));
    render(<MemoryRouter initialEntries={['/board?board=project:pr1']}><Board /></MemoryRouter>);

    expect(await screen.findByText('Write the launch post.')).toBeInTheDocument();
    expect(screen.getAllByText('Claimed by Writer').length).toBe(1);
    const second = screen.getByText('Check the prices.').closest('article')!;
    fireEvent.click(within(second).getByRole('button', { name: 'Claim' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/board/posts/c2/claim', {}));

    // Threads filter the channel.
    fireEvent.click(within(screen.getByRole('group', { name: 'Threads' })).getByRole('button', { name: 'Prices' }));
    await waitFor(() => expect(screen.queryByText('Write the launch post.')).not.toBeInTheDocument());

    // People and grants, a repeating post, a suggested command.
    expect((await screen.findAllByText('kofi@example.com')).length).toBeGreaterThan(0);
    const writer = screen.getAllByRole('checkbox', { name: 'Writer' })[0];
    fireEvent.click(writer);
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/board/channels/project%3Apr1', { agents: ['a2'] }));
    fireEvent.change(screen.getAllByLabelText('What it asks')[0], { target: { value: 'Check PRs' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Add repeating post' })[0]);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/board/schedules',
      { board: 'project:pr1', text: 'Check PRs', to: '', when: 'weekdays 09:00', kind: 'request' }));
    expect((await screen.findAllByText('weekdays at 09:00')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole('button', { name: '+ /review' })[0]);
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/board/commands/review',
      { agent_id: 'a1', prompt: 'Review it.', description: 'Review a change' }));
  });
});
