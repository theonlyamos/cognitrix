import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppAccessPanel } from './AppAccessPanel';
import { pageInApp, type AppAccessInfo, type AppDetail } from '@/lib/hostedApps';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api,
}));

const APP = { id: 'a1', slug: 'digest', title: 'Digest', version: 2, role: 'owner' } as AppDetail;
const INFO: AppAccessInfo = {
  apps: [{ id: 'm1', name: 'Gmail', account: 'amos@example.com', level: '', asked: true }],
  credentials: [
    { name: 'GITHUB_TOKEN', kind: 'token', description: '', allowed_hosts: ['api.github.com'], allowed: false, asked: false },
    { name: 'STRIPE', kind: 'token', description: '', allowed_hosts: ['api.stripe.com'], allowed: true, asked: false },
  ],
  secrets: [{ name: 'SLACK_HOOK', hint: '…9f2a' }],
  asked: { apps: ['Linear'], credentials: [], secrets: ['WEBHOOK_SECRET'] },
  paused: false, needs_restart: false, can_restart: true, full_api: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: INFO });
});

describe('AppAccessPanel', () => {
  it('shows what the app asked for and saves the owner\'s choices', async () => {
    api.put.mockResolvedValue({ data: INFO });
    render(<AppAccessPanel app={APP} onChanged={vi.fn()} />);
    expect(await screen.findByText('Gmail')).toBeInTheDocument();
    expect(screen.getByText(/asked for things you don't have yet: Linear/)).toBeInTheDocument();
    expect(screen.getByText('Only sent to api.github.com')).toBeInTheDocument();
    // A secret's value is never shown, only its last characters.
    expect(screen.getByText('…9f2a')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('What this app may do in Gmail'), { target: { value: 'read' } });
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/hosted-apps/a1/access', { apps: { m1: 'read' } }));

    fireEvent.click(screen.getByLabelText('Let this app use GITHUB_TOKEN'));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/hosted-apps/a1/access', { credentials: ['STRIPE', 'GITHUB_TOKEN'] }));

    // A secret the app asked for: one click fills in its name.
    fireEvent.click(screen.getByRole('button', { name: /WEBHOOK_SECRET/ }));
    expect(screen.getByLabelText('Secret name')).toHaveValue('WEBHOOK_SECRET');
    fireEvent.change(screen.getByLabelText('Secret value'), { target: { value: 'whsec_123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/hosted-apps/a1/secrets', { put: { WEBHOOK_SECRET: 'whsec_123' } }));
    await waitFor(() => expect(screen.getByLabelText('Secret value')).toHaveValue(''));

    fireEvent.click(screen.getByRole('button', { name: 'Remove SLACK_HOOK' }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/hosted-apps/a1/secrets', { remove: ['SLACK_HOOK'] }));
  });

  it('offers a restart when changes wait for one, and a way out of a pause', async () => {
    api.get.mockResolvedValue({ data: { ...INFO, paused: true, needs_restart: true } });
    api.put.mockResolvedValue({ data: { ...INFO, needs_restart: true } });
    api.post.mockResolvedValue({ data: { ...APP, version: 3 } });
    const onChanged = vi.fn();
    render(<AppAccessPanel app={APP} onChanged={onChanged} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Turn access back on' }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/hosted-apps/a1/access', { paused: false }));
    await waitFor(() => expect(screen.queryByText('Access is paused')).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Restart now' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/hosted-apps/a1/redeploy', expect.anything()));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ version: 3 })));
  });
});

describe('pageInApp', () => {
  it('keeps a page inside the app and drops anything that could leave it', () => {
    expect(pageInApp('reports/q3?tab=2')).toBe('reports/q3?tab=2');
    expect(pageInApp('/settings')).toBe('settings');
    expect(pageInApp('items?at=10:30')).toBe('items?at=10:30');
    expect(pageInApp(null)).toBe('');
    // Leading slashes go, so this is a page called evil.example inside the app, not another site.
    expect(pageInApp('//evil.example')).toBe('evil.example');
    for (const bad of ['https://evil.example', 'javascript:alert(1)', '../../api/v1/users', 'a\\b']) {
      expect(pageInApp(bad)).toBe('');
    }
  });
});
