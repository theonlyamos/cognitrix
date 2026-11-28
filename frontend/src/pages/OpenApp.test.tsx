import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import OpenApp from './OpenApp';

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, post },
}));

const notFound = () => Object.assign(new Error('no'), { isAxiosError: true, response: { status: 404, data: { detail: 'Not found' } } });

beforeEach(() => { [get, post].forEach((fn) => fn.mockReset()); });

function show(at: string, goTo: (url: string) => void) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Routes><Route path="/apps/open/:slug" element={<OpenApp goTo={goTo} />} /></Routes>
    </MemoryRouter>,
  );
}

describe('an app link opened signed in', () => {
  it('shows the app when the account can open it, without asking Tailscale', async () => {
    post.mockResolvedValue({ data: { url: '/a/board/~mine/' } });
    get.mockResolvedValue({ data: { id: 'a1', slug: 'board', title: 'Board', status: 'running' } });
    const goTo = vi.fn();
    show('/apps/open/board?at=items', goTo);
    await waitFor(() => expect(screen.getByTitle('Board')).toHaveAttribute('src', '/a/board/~mine/items'));
    expect(post).toHaveBeenCalledWith('/hosted-apps/board/open');
    expect(get).not.toHaveBeenCalledWith('/tailscale/apps/board');
    expect(goTo).not.toHaveBeenCalled();
  });

  it('falls back to the tailnet way in when the account has none', async () => {
    post.mockRejectedValue(notFound());
    get.mockImplementation((url: string) => url === '/tailscale/apps/board'
      ? Promise.resolve({ data: { url: '/a/board/~guest/', title: 'Board', login: 'ada@example.com' } })
      : Promise.reject(notFound()));
    const goTo = vi.fn();
    show('/apps/open/board?at=items', goTo);
    await waitFor(() => expect(goTo).toHaveBeenCalledWith('/a/board/~guest/items'));
    expect(screen.queryByText("This app didn't open")).toBeNull();
  });

  it('says it did not open when neither way works', async () => {
    post.mockRejectedValue(notFound());
    get.mockRejectedValue(notFound());
    const goTo = vi.fn();
    show('/apps/open/board', goTo);
    expect(await screen.findByRole('link', { name: 'Go to Apps' })).toBeInTheDocument();
    expect(goTo).not.toHaveBeenCalled();
  });
});
