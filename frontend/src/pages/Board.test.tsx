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
      board: 'agents', kind: 'decision', text: 'Launch moved to March.', to: 'Researcher', tags: '',
    }));
  });

  it('suggests agents after @, shows tags, and edits or withdraws your own post', async () => {
    posts.push(post({ id: 'p4', author_kind: 'person', author_name: 'You', author_id: '', text: 'Ask @Writer first.',
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
});
