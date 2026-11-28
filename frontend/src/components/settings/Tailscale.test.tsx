import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, fireEvent, waitFor } from '@testing-library/react';
import { TailscaleSettings } from './Tailscale';
import { hooksOrigin, linksOrigin, loadAddresses, tailscalePending, useAddresses, type TailscaleStatus } from '@/lib/tailscale';

const { get, put, post, del } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), del: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, put, post, delete: del },
}));

const NAME = 'studio.tail1234.ts.net';
const address = (over = {}) => ({ wanted: false, port: 443, active: false, url: '', problem: '', detail: '', needs_url: '', ...over });
const status = (over: Partial<TailscaleStatus> = {}): TailscaleStatus => ({
  enabled: true, mode: 'system', state: 'running', version: '1.94.1', name: NAME, machine: 'studio', tailnet: 'ada@example.com',
  ips: ['100.64.0.7'], user: 'ada@example.com', tags: [], key_expiry: '', https: true, funnel_allowed: true, login_url: '',
  hostname: 'cognitrix', can_sign_in: false, can_edit: true, door: 'direct', listen_local: false, signin: false,
  private: address({ wanted: true, active: true, url: `https://${NAME}` }),
  public: { ...address({ port: 8443 }), allow: ['webhooks'], ports: [443, 8443, 10000] },
  ...over,
});

let current: TailscaleStatus;
beforeEach(() => {
  [get, put, post, del].forEach((fn) => fn.mockReset());
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

  it('lets an admin turn on signing in from the tailnet', async () => {
    put.mockResolvedValue({ data: status({ signin: true }) });
    render(<TailscaleSettings />);
    const toggle = await screen.findByRole('checkbox', { name: 'Sign in from the tailnet' });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(put).toHaveBeenCalledWith('/tailscale', { signin: true }));
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Sign in from the tailnet' })).toBeChecked());
  });

  it('is read-only for people who are not admins', async () => {
    current = status({ can_edit: false });
    render(<TailscaleSettings />);
    expect(await screen.findByText('Only an admin can change these.')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Use Tailscale' })).toBeDisabled();
    expect(screen.queryByText('DEVICES AGENTS MAY USE')).toBeNull();
  });

  it("puts the agents' Fly computers on the tailnet with an OAuth client", async () => {
    const computers = { fly: true, scope: 'admins' as const, tag: 'tag:cognitrix-sandbox', oauth_id: '', oauth_saved: false, on_fly: false, policy: '"tag:cognitrix-sandbox": ["autogroup:admin"],' };
    current = status({ computers, devices: 'off' });
    put.mockResolvedValue({ data: status({ computers: { ...computers, oauth_id: 'kABC', oauth_saved: true }, devices: 'off' }) });
    post.mockResolvedValue({ data: { ok: false, steps: [
      { name: 'oauth', ok: true, detail: '' },
      { name: 'join_key', ok: false, detail: 'The OAuth client may not use tag:cognitrix-sandbox.' },
      { name: 'engine', ok: true, detail: '' },
    ] } });
    render(<TailscaleSettings />);
    expect(await screen.findByText('"tag:cognitrix-sandbox": ["autogroup:admin"],')).toBeInTheDocument();
    expect(screen.getByText(/isn't on Fly, so it reaches the computers over your tailnet/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check the connection' })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Client ID' }), { target: { value: ' kABC ' } });
    fireEvent.change(screen.getByLabelText('Client secret'), { target: { value: 'tskey-client-kABC-xyz' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/tailscale', { oauth_id: 'kABC', oauth_secret: 'tskey-client-kABC-xyz' }));
    // The secret never comes back: the box only says one is saved.
    await waitFor(() => expect(screen.getByLabelText('Client secret')).toHaveAttribute('placeholder', 'Secret saved. Type a new one to replace it.'));
    expect((screen.getByLabelText('Client secret') as HTMLInputElement).value).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Check the connection' }));
    expect(await screen.findByText("It can't make a join key with the computers' tag.")).toBeInTheDocument();
    expect(screen.getByText('The OAuth client may not use tag:cognitrix-sandbox.')).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith('/tailscale/computers/check');
  });

  it('hides the Fly computers card when computers run elsewhere', async () => {
    current = status({ computers: { fly: false, scope: 'nobody', tag: 'tag:cognitrix-sandbox', oauth_id: '', oauth_saved: false, on_fly: false, policy: '' }, devices: 'off' });
    render(<TailscaleSettings />);
    expect(await screen.findByText('DEVICES AGENTS MAY USE')).toBeInTheDocument();
    expect(screen.queryByText("AGENTS' COMPUTERS ON YOUR TAILNET")).toBeNull();
    expect(get).not.toHaveBeenCalledWith('/tailscale/devices');   // off: the list isn't even asked for
  });

  it('lets an admin allow a tailnet device with its login names, and take it off again', async () => {
    const device = (over = {}) => ({ node: 'nNAS', name: 'nas', dns: 'nas.tail1234.ts.net', os: 'linux', online: true, tags: [], tailscale_ssh: true, present: true, allowed: false, users: [], path: 'tailscale', ...over });
    let devices = [device(), device({ node: 'nWIN', name: 'Office-PC', os: 'windows', tailscale_ssh: false })];
    current = status({ devices: 'admins' });
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/tailscale' ? current : url === '/tailscale/devices' ? { scope: 'admins', devices, key: 'ssh-ed25519 AAAAC3 cognitrix', ssh: true } : {},
    }));
    put.mockImplementation(() => {
      devices = [device({ allowed: true, users: ['ada', 'deploy'] }), devices[1]];
      return Promise.resolve({ data: devices[0] });
    });
    del.mockImplementation(() => { devices = [device(), devices[1]]; return Promise.resolve({ data: { removed: true } }); });
    render(<TailscaleSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Allow nas' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Login names agents may use' }), { target: { value: 'ada, deploy ada' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/tailscale/devices/nNAS', { users: ['ada', 'deploy'], path: 'tailscale' }));
    expect(await screen.findByText('Agents run commands as ada, deploy, over Tailscale SSH.')).toBeInTheDocument();
    expect(screen.getByText('ssh-ed25519 AAAAC3 cognitrix')).toBeInTheDocument();

    // A machine without Tailscale SSH starts on this server's key.
    fireEvent.click(screen.getByRole('button', { name: 'Allow Office-PC' }));
    expect((screen.getByRole('combobox', { name: 'How this server signs in' }) as HTMLSelectElement).value).toBe('key');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    fireEvent.click(screen.getByRole('button', { name: 'Remove nas' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('/tailscale/devices/nNAS'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Allow nas' })).toBeInTheDocument());
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
