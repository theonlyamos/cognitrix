import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { TailnetGuest } from './TailnetGuest';

const get = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get },
}));

beforeEach(() => { get.mockReset(); });

function show(at: string, goTo: (url: string) => void) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Routes><Route path="/apps/open/:slug" element={<TailnetGuest goTo={goTo} otherwise={<div>sign in first</div>} />} /></Routes>
    </MemoryRouter>,
  );
}

describe('an app link opened signed out', () => {
  it('opens the app for someone on the tailnet it was shared with', async () => {
    get.mockResolvedValue({ data: { url: '/a/board/~tok/', title: 'Board', login: 'ada@example.com' } });
    const goTo = vi.fn();
    show('/apps/open/board?bare=1&at=items%3Fq%3D1', goTo);
    await waitFor(() => expect(goTo).toHaveBeenCalledWith('/a/board/~tok/items?q=1'));
    expect(get).toHaveBeenCalledWith('/tailscale/apps/board');
    expect(screen.queryByText('sign in first')).toBeNull();
  });

  it('never leaves for another site', async () => {
    get.mockResolvedValue({ data: { url: '/a/board/~tok/' } });
    const goTo = vi.fn();
    show('/apps/open/board?at=https%3A%2F%2Fevil.example%2F', goTo);
    await waitFor(() => expect(goTo).toHaveBeenCalledWith('/a/board/~tok/'));
  });

  it('asks everyone else to sign in', async () => {
    get.mockRejectedValue(Object.assign(new Error('no'), { isAxiosError: true, response: { status: 404, data: { detail: 'Not found' } } }));
    const goTo = vi.fn();
    show('/apps/open/board', goTo);
    expect(await screen.findByText('sign in first')).toBeInTheDocument();
    expect(goTo).not.toHaveBeenCalled();
  });
});
