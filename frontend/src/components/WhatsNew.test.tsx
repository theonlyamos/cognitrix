import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import WhatsNew, { OPEN_WHATS_NEW_EVENT } from './WhatsNew';
import { AGENTS_CHANGED_EVENT } from '@/lib/providers';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost },
  API_BASE: '/api/v1',
  errorMessage: (_e: unknown, fallback: string) => fallback,
}));

const status = (over: Record<string, unknown> = {}) => ({
  version: '0.5.6',
  show: true,
  releases: [
    { version: '0.5.6', notes: '## Highlights\n- Shiny new thing' },
    { version: '0.5.5', notes: '## Highlights\n- Older thing' },
  ],
  starter_team: [],
  releases_url: 'https://github.com/theonlyamos/cognitrix/releases',
  ...over,
});

function renderIt() {
  return render(
    <MemoryRouter>
      <WhatsNew />
    </MemoryRouter>,
  );
}

describe("What's new window", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiPost.mockResolvedValue({ data: {} });
  });

  it('stays hidden when there is nothing new', async () => {
    apiGet.mockResolvedValue({ data: status({ show: false, releases: [] }) });
    renderIt();
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/whats-new'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows every release since the last one seen and marks it seen on close', async () => {
    apiGet.mockResolvedValue({ data: status() });
    renderIt();
    expect(await screen.findByRole('dialog', { name: 'Cognitrix was updated' })).toBeInTheDocument();
    expect(screen.getByText(/everything new since 0\.5\.5/)).toBeInTheDocument();
    expect(screen.getByText('Shiny new thing')).toBeInTheDocument();
    expect(screen.getByText('Older thing')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'All release notes' })).toHaveAttribute('href', status().releases_url);

    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    expect(apiPost).toHaveBeenCalledWith('/whats-new/seen');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('Esc closes it and marks it seen', async () => {
    apiGet.mockResolvedValue({ data: status() });
    renderIt();
    await screen.findByRole('dialog');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(apiPost).toHaveBeenCalledWith('/whats-new/seen');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('offers to add missing starter agents', async () => {
    apiGet.mockResolvedValue({
      data: status({
        starter_team: [
          { key: 'coder', name: 'Coder', description: '' },
          { key: 'writer', name: 'Writer', description: '' },
        ],
      }),
    });
    apiPost.mockResolvedValue({ data: { created: [{ id: 'a', name: 'Coder' }, { id: 'b', name: 'Writer' }] } });
    const changed = vi.fn();
    window.addEventListener(AGENTS_CHANGED_EVENT, changed);
    renderIt();

    expect(await screen.findByText(/Coder, Writer: specialists/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add 2 agents' }));
    expect(await screen.findByText('Starter team added.')).toBeInTheDocument();
    expect(apiPost).toHaveBeenCalledWith('/onboarding/starter-team');
    expect(changed).toHaveBeenCalled();
    window.removeEventListener(AGENTS_CHANGED_EVENT, changed);
  });

  it('opens from the menu with recent notes and does not mark anything seen', async () => {
    apiGet.mockImplementation((url: string) =>
      Promise.resolve({ data: url === '/whats-new' ? status({ show: false, releases: [] }) : status({ show: undefined }) }),
    );
    renderIt();
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/whats-new'));
    act(() => {
      window.dispatchEvent(new Event(OPEN_WHATS_NEW_EVENT));
    });
    expect(await screen.findByRole('dialog', { name: "What's new in Cognitrix" })).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith('/whats-new/notes');
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    expect(apiPost).not.toHaveBeenCalled();
  });
});
