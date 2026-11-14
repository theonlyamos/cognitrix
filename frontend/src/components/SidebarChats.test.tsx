import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SidebarChats from './SidebarChats';
import { api } from '@/lib/api';
import { CHATS_EDITED, setChatNav } from '@/lib/chatNav';

const CHATS = vi.hoisted(() => [
  { id: 'c1', title: 'Trip to Accra', message_count: 4, kind: 'agent', agent_id: 'agent-1', team_id: null, updated_at: '2026-11-03T10:00:00Z', started_by: { agent_id: 'agent-2', agent_name: 'Writer' } },
  { id: 'c2', title: 'Budget review', message_count: 2, kind: 'agent', agent_id: 'agent-1', team_id: null, updated_at: '2026-11-01T10:00:00Z', pinned: true },
  { id: 'c3', title: 'Old notes', message_count: 2, kind: 'agent', agent_id: 'agent-1', team_id: null, updated_at: '2026-10-01T10:00:00Z', archived: true },
  { id: 'c4', title: 'Launch kickoff', message_count: 2, kind: 'team', agent_id: null, team_id: 'team-1', updated_at: '2026-11-02T10:00:00Z' },
  { id: 'c5', title: 'Blog titles', message_count: 2, kind: 'agent', agent_id: 'agent-2', team_id: null, updated_at: '2026-11-02T12:00:00Z', running: true },
]);
const apiPatch = vi.hoisted(() => vi.fn());
const refetch = vi.hoisted(() => vi.fn());
const apiDelete = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({ data: [] }), post: vi.fn().mockResolvedValue({ data: {} }), patch: apiPatch, delete: apiDelete },
  errorMessage: (_e: unknown, fallback = 'error') => fallback,
}));
vi.mock('@/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path === '/agents' ? [{ id: 'agent-1', name: 'Agent One' }, { id: 'agent-2', name: 'Writer' }]
      : path === '/teams' ? [{ id: 'team-1', name: 'Launch crew' }]
        : path === '/sessions/chats' ? CHATS : [],
    loading: false,
    loadedPath: path,
    refetch,
    setData: vi.fn(),
  }),
}));

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname}{location.search}</div>;
}

function renderList(collapsed = false) {
  return render(
    <MemoryRouter initialEntries={['/dashboard']} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <SidebarChats collapsed={collapsed} onExpand={vi.fn()} />
      <Routes><Route path="*" element={<Where />} /></Routes>
    </MemoryRouter>,
  );
}

const recentTitles = () =>
  within(screen.getByRole('list', { name: 'Recent chats' })).getAllByRole('listitem').map((li) => li.textContent || '');

describe('SidebarChats', () => {
  beforeEach(() => {
    apiPatch.mockReset().mockResolvedValue({ data: {} });
    apiDelete.mockReset().mockResolvedValue({ data: {} });
    localStorage.clear();
    setChatNav({ activeChatId: null, runningIds: [] });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('lists recent chats newest first with pinned on top and archived tucked away', async () => {
    renderList();
    const titles = recentTitles();
    expect(titles[0]).toContain('Budget review');
    expect(titles[1]).toContain('Trip to Accra');
    expect(titles.some((t) => t.includes('Launch kickoff'))).toBe(true);
    expect(screen.queryByText('Old notes')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Archived (1)' }));
    expect(screen.getByText('Old notes')).toBeInTheDocument();
  });

  it('says when an agent started a chat', () => {
    renderList();
    expect(screen.getByRole('button', { name: /^Agent One: Trip to Accra/ })).toHaveAttribute('title', 'Trip to Accra · started by Writer');
  });

  it('shows which chat is open and which are running', () => {
    setChatNav({ activeChatId: 'c1', runningIds: ['c2'] });
    renderList();
    expect(screen.getByRole('button', { current: 'page' })).toHaveTextContent('Trip to Accra');
    expect(screen.getByTestId('running-c2')).toBeInTheDocument();
    // Running on the server with another agent.
    expect(screen.getByTestId('running-c5')).toBeInTheDocument();
  });

  it('keeps re-reading the list while a chat is running somewhere', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderList();
      refetch.mockClear();
      await act(async () => { vi.advanceTimersByTime(4_100); });
      expect(refetch).toHaveBeenCalledWith({ silent: true });
    } finally {
      vi.useRealTimers();
    }
  });

  it('re-reads the list when a chat is made elsewhere, e.g. by another agent', async () => {
    const get = vi.mocked(api.get);
    let version = 'v1';
    let listFails = false;
    get.mockImplementation(async (path: string) => {
      if (path === '/sessions/chats/version') return { data: { version } } as never;
      if (path === '/sessions/chats' && listFails) throw new Error('offline');
      return { data: [] } as never;
    });
    const listReads = () => get.mock.calls.filter(([path]) => path === '/sessions/chats').length;
    CHATS[4].running = false;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderList();
      await act(async () => { await Promise.resolve(); });
      // Nothing changed: no re-read.
      await act(async () => { vi.advanceTimersByTime(4_100); });
      expect(get).toHaveBeenCalledWith('/sessions/chats/version');
      expect(listReads()).toBe(0);
      // An agent started a chat with another agent, but the re-read fails.
      version = 'v2';
      listFails = true;
      await act(async () => { vi.advanceTimersByTime(4_100); });
      expect(listReads()).toBe(1);
      // The failed re-read is tried again on the next tick.
      listFails = false;
      await act(async () => { vi.advanceTimersByTime(4_100); });
      expect(listReads()).toBe(2);
      // Once it succeeded, a stable token means no more re-reads.
      await act(async () => { vi.advanceTimersByTime(4_100); });
      expect(listReads()).toBe(2);
    } finally {
      vi.useRealTimers();
      CHATS[4].running = true;
      get.mockReset().mockResolvedValue({ data: [] });
    }
  });

  it('opens a chat on the chat page, and team chats on the team page', async () => {
    renderList();
    await userEvent.click(screen.getByRole('button', { name: /^Agent One: Trip to Accra/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/home?chat=c1');
    await userEvent.click(screen.getByRole('button', { name: /^Launch crew: Launch kickoff/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/teams/team-1/chat?chat=c4');
  });

  it('groups chats by agent and remembers the choice and closed sections', async () => {
    renderList();
    await userEvent.click(screen.getByRole('button', { name: 'By agent' }));
    expect(localStorage.getItem('chatList.mode')).toBe('agent');
    expect(screen.getByRole('region', { name: 'Agent One chats' })).toHaveTextContent('Trip to Accra');
    expect(screen.getByRole('region', { name: 'Launch crew chats' })).toHaveTextContent('Launch kickoff');
    await userEvent.click(screen.getByRole('button', { name: /^Agent One/, expanded: true }));
    expect(screen.queryByText('Trip to Accra')).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem('chatList.closedSections') || '[]')).toContain('agent:agent-1');
    // Pinned chats stay on top even with their agent's section closed.
    expect(screen.getByText('Budget review')).toBeInTheDocument();
  });

  it('starts a new chat with an agent from its section', async () => {
    localStorage.setItem('chatList.mode', 'agent');
    renderList();
    await userEvent.click(screen.getByRole('button', { name: 'New chat with Writer' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/home?new=agent%3Aagent-2');
  });

  it('renames, pins, archives and deletes from the row menu and tells the chat page', async () => {
    const edited = vi.fn();
    window.addEventListener(CHATS_EDITED, edited);
    renderList();
    await userEvent.click(screen.getByRole('button', { name: 'More actions for Trip to Accra' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Pin to top' }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith('/sessions/c1', { pinned: true }));
    await waitFor(() => expect(edited).toHaveBeenCalled());

    await userEvent.click(screen.getByRole('button', { name: 'More actions for Trip to Accra' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Rename' }));
    const box = screen.getByRole('textbox', { name: 'Chat name' });
    await userEvent.clear(box);
    await userEvent.type(box, 'Ghana holiday{Enter}');
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith('/sessions/c1', { title: 'Ghana holiday' }));

    await userEvent.click(screen.getByRole('button', { name: 'More actions for Budget review' }));
    expect(screen.getByRole('menuitem', { name: 'Unpin' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Archive' }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith('/sessions/c2', { archived: true }));

    edited.mockClear();
    await userEvent.click(screen.getByRole('button', { name: 'More actions for Trip to Accra' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/sessions/c1'));
    await waitFor(() => expect((edited.mock.calls[0][0] as CustomEvent).detail).toEqual({ deletedId: 'c1' }));
    window.removeEventListener(CHATS_EDITED, edited);
  });

  it('collapses to agent thumbnails that open a flyout of chats', async () => {
    renderList(true);
    await userEvent.click(screen.getByRole('button', { name: 'Writer, 1 chat' }));
    const flyout = screen.getByRole('dialog', { name: 'Writer chats' });
    await userEvent.click(within(flyout).getByRole('button', { name: /Blog titles/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/home?chat=c5');
  });
});
