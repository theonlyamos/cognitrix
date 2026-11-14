import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Apps from './Apps';
import { AppCard } from '@/components/hosted-apps/AppCard';
import { publishedAppFrom, savedAppsView } from '@/lib/hostedApps';

const apiGet = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get: apiGet, post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const app = (over: Record<string, unknown>) => ({
  id: 'a1', slug: 'support-log-abc', title: 'Support log', description: 'Calls this week', status: 'online', version: 3, latest: 3,
  last_error: '', runtime: 'sandbox', branch: '', role: 'owner', owner: { id: 'u1', name: 'Ada' }, agent: { id: 'g1', name: 'Builder' },
  session_id: 's1', task_id: '', access: 'private', shared_with: {}, pinned: false, sleep_after: 15, visits_7d: [1, 2, 0, 4, 3, 1, 2],
  uses_api: false, command: 'python app.py', install: '', folder: '/work/support', link: '/apps/open/support-log-abc', thumb_url: '',
  created: '2026-11-12 10:00:00', updated: '2026-11-13 10:00:00', ...over,
});

const LIST = { runtime: 'sandbox', apps: [app({}), app({ id: 'a2', slug: 'okr-x', title: 'OKR tracker', status: 'asleep', access: 'workspace' })] };

function show() {
  return render(
    <MemoryRouter initialEntries={['/apps']}>
      <Routes>
        <Route path="/apps" element={<Apps />} />
        <Route path="/apps/:id" element={<div>one app</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Apps page', () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiGet.mockImplementation(() => Promise.resolve({ data: LIST }));
  });

  it('shows live cards by default and remembers the table', async () => {
    show();
    expect((await screen.findAllByText('Support log')).length).toBeGreaterThan(0);
    expect(screen.getByRole('radio', { name: 'Cards' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Table' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(savedAppsView()).toBe('table');
  });

  it('filters to asleep apps', async () => {
    show();
    await screen.findAllByText('OKR tracker');
    fireEvent.click(screen.getByRole('tab', { name: /Asleep/ }));
    expect(screen.queryAllByText('Support log')).toHaveLength(0);
    expect(screen.getAllByText('OKR tracker').length).toBeGreaterThan(0);
  });
});

describe('Publish App card', () => {
  const result = 'Published "Support log" (app_id a1, version 3). It is live at /apps/open/support-log-abc.';

  it('reads what was published', () => {
    expect(publishedAppFrom('publish_app', result)).toEqual({ id: 'a1', title: 'Support log', version: 3 });
    expect(publishedAppFrom('Publish App', result)).not.toBeNull();
    expect(publishedAppFrom('publish_artifact', result)).toBeNull();
  });

  it('opens the app page', () => {
    render(
      <MemoryRouter initialEntries={['/home']}>
        <Routes>
          <Route path="/home" element={<AppCard toolName="publish_app" result={result} />} />
          <Route path="/apps/:id" element={<div>one app</div>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /Support log/ }));
    expect(screen.getByText('one app')).toBeInTheDocument();
  });
});

describe('Full-screen app', () => {
  it('shows a way back to Cognitrix and leaves on Esc', async () => {
    const { default: OpenApp } = await import('./OpenApp');
    const { api } = await import('@/lib/api');
    vi.mocked(api.post).mockResolvedValue({ data: { url: '/a/support-log-abc/~tok/' } });
    apiGet.mockImplementation(() => Promise.resolve({ data: app({}) }));
    render(
      <MemoryRouter initialEntries={['/apps/open/support-log-abc']}>
        <Routes>
          <Route path="/apps/open/:slug" element={<OpenApp />} />
          <Route path="/apps/:id" element={<div>app page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByTitle('Support log')).toHaveAttribute('src', '/a/support-log-abc/~tok/');
    expect(screen.getByRole('link', { name: /Back/ })).toHaveAttribute('href', '/apps/a1');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(await screen.findByText('app page')).toBeInTheDocument();
  });
});

describe('App in its own window', () => {
  it('closes instead of going back', async () => {
    const { default: OpenApp } = await import('./OpenApp');
    const { api } = await import('@/lib/api');
    vi.mocked(api.post).mockResolvedValue({ data: { url: '/a/support-log-abc/~tok/' } });
    apiGet.mockImplementation(() => Promise.resolve({ data: app({}) }));
    const close = vi.spyOn(window, 'close').mockImplementation(() => undefined);
    render(
      <MemoryRouter initialEntries={['/apps/open/support-log-abc?window=1']}>
        <Routes><Route path="/apps/open/:slug" element={<OpenApp />} /></Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: /Close/ }));
    expect(close).toHaveBeenCalled();
    close.mockRestore();
  });
});
