import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, fireEvent, waitFor } from '@testing-library/react';
import { TailscaleSettings } from './Tailscale';
import { hooksOrigin, linksOrigin, loadAddresses, tailscalePending, useAddresses, type TailscaleStatus } from '@/lib/tailscale';

const { get, put, post } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, put, post },
}));

const NAME = 'studio.tail1234.ts.net';
const address = (over = {}) => ({ wanted: false, port: 443, active: false, url: '', problem: '', detail: '', needs_url: '', ...over });
const status = (over: Partial<TailscaleStatus> = {}): TailscaleStatus => ({
  enabled: true, mode: 'system', state: 'running', version: '1.94.1', name: NAME, machine: 'studio', tailnet: 'ada@example.com',
  ips: ['100.64.0.7'], user: 'ada@example.com', tags: [], key_expiry: '', https: true, funnel_allowed: true, login_url: '',
  hostname: 'cognitrix', can_sign_in: false, can_edit: true, door: 'direct', listen_local: false,
  private: address({ wanted: true, active: true, url: `https://${NAME}` }),
  public: { ...address({ port: 8443 }), allow: ['webhooks'], ports: [443, 8443, 10000] },
  ...over,
});

let current: TailscaleStatus;
beforeEach(() => {
  [get, put, post].forEach((fn) => fn.mockReset());
  current = status();
  get.mockImplementation((url: string) => Promise.resolve({ data: url === '/tailscale' ? current : {} }));
});

describe('TailscaleSettings', () => {
  it('shows the private address and turns the public one on', async () => {
    put.mockResolvedValue({ data: status({
      public: { ...address({ wanted: true, active: true, port: 8443, url: `https://${NAME}:8443` }), allow: ['webhooks'], ports: [443, 8443, 10000] },
    }) });
    render(<TailscaleSettings />);
    expect(await screen.findByText(`https://${NAME}`)).toBeInTheDocument();
    expect(screen.getByText('Connected')).toBeInTheDocument();
    expect(screen.getByText(/Signed in as ada@example.com/)).toBeInTheDocument();
    // Passkeys are per address, and this page is open somewhere else.
    expect(screen.getByText(/To sign in at this address with a passkey, open it and add one there/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Public address' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/tailscale', { public: true }));
    expect(await screen.findByText(`https://${NAME}:8443`)).toBeInTheDocument();
    // Webhooks only unless the owner widens it.
    expect((screen.getByRole('combobox', { name: 'What it lets through' }) as HTMLSelectElement).value).toBe('webhooks');
    fireEvent.change(screen.getByRole('combobox', { name: 'What it lets through' }), { target: { value: 'links' } });
    await waitFor(() => expect(put).toHaveBeenCalledWith('/tailscale', { allow: ['webhooks', 'links'] }));
  });

  it('says what to do when Tailscale needs something from the owner', async () => {
    current = status({
      private: address({ wanted: true, problem: 'needs_operator' }),
      public: { ...address({ wanted: true, port: 8443, problem: 'needs_turning_on', needs_url: 'https://login.tailscale.com/f/funnel?node=abc' }), allow: ['webhooks'], ports: [443, 8443, 10000] },
    });
    render(<TailscaleSettings />);
    expect(await screen.findByText('sudo tailscale set --operator=$USER')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Turn it on at Tailscale' })).toHaveAttribute('href', 'https://login.tailscale.com/f/funnel?node=abc');
    expect(tailscalePending(current)).toBe(true);
  });

  it('a server signs its own Tailscale in; a computer with Tailscale is told to open it', async () => {
    current = status({ state: 'needs_login', name: '', can_sign_in: true, mode: 'builtin' });
    post.mockResolvedValue({ data: status({ state: 'needs_login', name: '', can_sign_in: true, mode: 'builtin', login_url: 'https://login.tailscale.com/a/abc123' }) });
    const { unmount } = render(<TailscaleSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in to Tailscale' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/tailscale/connect', { auth_key: '' }));
    expect(await screen.findByRole('link', { name: 'https://login.tailscale.com/a/abc123' })).toBeInTheDocument();
    unmount();

    current = status({ state: 'needs_login', name: '' });
    render(<TailscaleSettings />);
    expect(await screen.findByText(/Open Tailscale on this computer and sign in/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sign in to Tailscale' })).toBeNull();
  });

  it('says when Tailscale has not taken a switched-off address down yet', async () => {
    current = status({
      public: { ...address({ port: 8443, problem: 'not_removed', detail: 'Access denied: serve config denied' }), allow: ['webhooks'], ports: [443, 8443, 10000] },
    });
    render(<TailscaleSettings />);
    expect(await screen.findByText(/Nothing that arrives through it is let in/)).toBeInTheDocument();
    expect(screen.getByText(/Tailscale said: Access denied: serve config denied/)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Public address' })).not.toBeChecked();
    expect(tailscalePending(current)).toBe(true);   // the page keeps looking until it is gone
  });

  it('tells everything that shows addresses when one comes up later', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const waiting = { ...address({ wanted: true, port: 8443, problem: 'needs_turning_on', needs_url: 'https://login.tailscale.com/f/funnel?node=abc' }), allow: ['webhooks' as const], ports: [443, 8443, 10000] };
      const addresses = (hooks: string) => ({ configured: '', private: `https://${NAME}`, public: hooks, hooks, links: '', public_passes: ['webhooks'] });
      let info = addresses('');
      current = status({ public: waiting });
      get.mockImplementation((url: string) => Promise.resolve({ data: url === '/tailscale' ? current : url === '/system/info' ? { addresses: info } : {} }));
      await act(async () => { await loadAddresses(true); });
      const shown = renderHook(() => useAddresses());       // a webhook card somewhere else on the page
      expect(shown.result.current?.hooks).toBe('');
      render(<TailscaleSettings />);
      expect(await screen.findByRole('link', { name: 'Turn it on at Tailscale' })).toBeInTheDocument();
      // The owner turns Funnel on at Tailscale; the next look finds the address working.
      info = addresses(`https://${NAME}:8443`);
      current = status({ public: { ...waiting, active: true, problem: '', needs_url: '', url: `https://${NAME}:8443` } });
      await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
      expect(await screen.findByText(`https://${NAME}:8443`)).toBeInTheDocument();
      await waitFor(() => expect(shown.result.current?.hooks).toBe(`https://${NAME}:8443`));
    } finally {
      vi.useRealTimers();
    }
  });

  it('is read-only for people who are not admins', async () => {
    current = status({ can_edit: false });
    render(<TailscaleSettings />);
    expect(await screen.findByText('Only an admin can change these.')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Use Tailscale' })).toBeDisabled();
  });
});

describe('addresses', () => {
  it('uses the public address for webhooks and public links when the server has one', () => {
    const a = { configured: '', private: `https://${NAME}`, public: `https://${NAME}:8443`, hooks: `https://${NAME}:8443`, links: '', public_passes: ['webhooks'] };
    expect(hooksOrigin(a, 'http://localhost:8000')).toBe(`https://${NAME}:8443`);
    expect(linksOrigin(a, 'http://localhost:8000')).toBe('http://localhost:8000');
    expect(hooksOrigin(null, 'http://localhost:8000/')).toBe('http://localhost:8000');
  });
});
