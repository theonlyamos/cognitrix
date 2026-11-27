import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AppShareDialog } from './AppShareDialog';
import type { AppDetail } from '@/lib/hostedApps';
import { loadAddresses } from '@/lib/tailscale';

const { get, put } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, put },
}));

const app = (over: Record<string, unknown> = {}) => ({
  id: 'a1', slug: 'board', title: 'Board', role: 'owner', owner: { id: 'u1', name: 'Ada' }, access: 'private',
  shared_with: {}, people: [{ id: 'u2', name: 'Bea' }], tailnet: { everyone: false, logins: [] }, ...over,
}) as unknown as AppDetail;

let addresses: Record<string, unknown> | null;
beforeEach(async () => {
  [get, put].forEach((fn) => fn.mockReset());
  get.mockImplementation(() => Promise.resolve({ data: { addresses } }));
  await at('https://studio.tail1234.ts.net');
});

/** The server's Tailscale private address ('' when it has none). */
async function at(privateAddress: string) {
  addresses = { private: privateAddress };
  await loadAddresses(true);
}

function show(current: AppDetail) {
  const onChanged = vi.fn();
  render(<AppShareDialog app={current} onClose={() => undefined} onChanged={onChanged} />);
  return onChanged;
}

describe('sharing an app with your tailnet', () => {
  it('adds a Tailscale login and shows the link to send them', async () => {
    const shared = app({ access: 'tailnet', tailnet: { everyone: false, logins: ['eve@example.com'] } });
    put.mockResolvedValue({ data: shared });
    const onChanged = show(app());
    fireEvent.change(await screen.findByLabelText('Tailscale login to add'), { target: { value: ' Eve@Example.com ' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Add' })[1]);
    await waitFor(() => expect(put).toHaveBeenCalledWith('/hosted-apps/a1/sharing', { tailnet: ['eve@example.com'] }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(shared));
  });

  it('lists who has it, with the private address link, and removes one', async () => {
    put.mockResolvedValue({ data: app() });
    show(app({ tailnet: { everyone: false, logins: ['bob@github', 'eve@example.com'] } }));
    expect(await screen.findByText('https://studio.tail1234.ts.net/apps/open/board')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove bob@github' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/hosted-apps/a1/sharing', { tailnet: ['eve@example.com'] }));
  });

  it('shares with everyone on the tailnet and keeps the logins for later', async () => {
    put.mockResolvedValue({ data: app() });
    show(app({ tailnet: { everyone: false, logins: ['eve@example.com'] } }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /Everyone on your tailnet/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/hosted-apps/a1/sharing', { tailnet: ['*', 'eve@example.com'] }));
  });

  it('is not offered without a private address, and says when one that was used is off', async () => {
    await at('');
    const { unmount } = render(<AppShareDialog app={app()} onClose={() => undefined} onChanged={() => undefined} />);
    expect(await screen.findByText('General access')).toBeInTheDocument();
    expect(screen.queryByText('Your tailnet')).toBeNull();
    unmount();
    show(app({ tailnet: { everyone: true, logins: [] } }));
    expect(await screen.findByText(/The Tailscale private address is off/)).toBeInTheDocument();
  });
});
