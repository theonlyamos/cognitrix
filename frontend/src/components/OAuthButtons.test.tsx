import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import OAuthButtons from './OAuthButtons';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const checkAuth = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
const openExternal = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
const desktopMode = vi.hoisted(() => ({ on: false }));

vi.mock('@/lib/api', () => ({ api: { get: apiGet, post: apiPost }, API_BASE: '/api/v1' }));
vi.mock('@/context/AppContext', () => ({ useUser: () => ({ checkAuth }) }));
vi.mock('@/lib/desktop', () => ({ isDesktop: () => desktopMode.on, desktop: { openExternal } }));

function renderButtons(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <OAuthButtons mode="signin" />
    </MemoryRouter>,
  );
}

describe('Google and GitHub sign-in buttons', () => {
  beforeEach(() => {
    apiGet.mockReset().mockResolvedValue({ data: { google: true, github: false } });
    apiPost.mockReset();
    localStorage.clear();
    desktopMode.on = false;
  });
  afterEach(() => vi.useRealTimers());

  it('shows only the providers the server has set up', async () => {
    renderButtons();
    expect(await screen.findByRole('button', { name: 'Continue with Google' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /GitHub/ })).not.toBeInTheDocument();
  });

  it('shows nothing when no provider is set up, but still shows a returned error', async () => {
    apiGet.mockResolvedValue({ data: { google: false, github: false } });
    const { container } = renderButtons();
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();

    renderButtons('/?oauth_error=No%20Cognitrix%20account%20uses%20a%40b.c');
    expect(await screen.findByRole('alert')).toHaveTextContent('No Cognitrix account uses a@b.c');
  });

  it('signs in through the browser from the desktop app', async () => {
    desktopMode.on = true;
    apiPost
      .mockResolvedValueOnce({ data: { status: 'pending' } })
      .mockResolvedValueOnce({ data: { status: 'done', access_token: 'tok-1' } });
    renderButtons();
    const button = await screen.findByRole('button', { name: 'Continue with Google' });

    vi.useFakeTimers();
    fireEvent.click(button);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(openExternal).toHaveBeenCalledWith(expect.stringMatching(/\/api\/v1\/auth\/oauth\/google\/start\?poll=[0-9a-f]{48}$/));
    expect(screen.getByRole('status')).toHaveTextContent('Finish signing in in your browser');

    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    vi.useRealTimers();
    await waitFor(() => expect(checkAuth).toHaveBeenCalled());
    expect(localStorage.getItem('token')).toBe('tok-1');
    const poll = openExternal.mock.calls[0][0].split('poll=')[1];
    expect(apiPost).toHaveBeenCalledWith('/auth/oauth/poll', { poll });
  });
});
