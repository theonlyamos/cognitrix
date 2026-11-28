import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { TailnetSignIn } from './TailnetSignIn';
import { loadAddresses } from '@/lib/tailscale';

const { get, put, del } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), del: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, put, delete: del },
}));

const link = (over = {}) => ({ enabled: true, login: '', name: '', picture: '', can_sign_in: false, linked: '', linked_at: '', ...over });
let current = link();
let addresses: Record<string, unknown> | null = null;

beforeEach(async () => {
  [get, put, del].forEach((fn) => fn.mockReset());
  current = link();
  addresses = null;
  get.mockImplementation((url: string) => Promise.resolve({ data: url === '/tailscale/link' ? current : { addresses } }));
  await loadAddresses(true);
});

describe('Settings › Sign-ins › Tailscale', () => {
  it('is not there until an admin turns it on', async () => {
    current = link({ enabled: false });
    const { container } = render(<TailnetSignIn />);
    await waitFor(() => expect(get).toHaveBeenCalledWith('/tailscale/link'));
    expect(container).toBeEmptyDOMElement();
  });

  it('links the login Tailscale says this device is, after saying what that means', async () => {
    current = link({ login: 'ada@example.com' });
    put.mockResolvedValue({ data: link({ login: 'ada@example.com', linked: 'ada@example.com', can_sign_in: true }) });
    render(<TailnetSignIn />);
    expect(await screen.findByText(/Tailscale says this device is ada@example.com/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Link' }));
    expect(put).not.toHaveBeenCalled();
    expect(screen.getByText(/Anyone who can use a device signed in to Tailscale as ada@example.com/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Link ada@example.com' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/tailscale/link'));
    expect(await screen.findByText(/Linked to ada@example.com/)).toBeInTheDocument();
    expect(screen.getByText('On')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Link' })).toBeNull();
  });

  it('away from the Tailscale address it says where to go', async () => {
    addresses = { private: 'https://studio.tail1234.ts.net/' };
    await loadAddresses(true);
    render(<TailnetSignIn />);
    expect(await screen.findByText(/open this page at the server's Tailscale address/)).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'Open it there' })).toHaveAttribute('href', 'https://studio.tail1234.ts.net/settings/sign-ins');
    expect(screen.queryByRole('button', { name: 'Link' })).toBeNull();
  });

  it('unlinks, and a link from before can still be removed when the server turned it off', async () => {
    current = link({ enabled: false, linked: 'ada@example.com' });
    del.mockResolvedValue({ data: link({ enabled: false }) });
    const { container } = render(<TailnetSignIn />);
    expect(await screen.findByText(/Turned off on this server/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Unlink' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('/tailscale/link'));
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('shows why linking failed', async () => {
    current = link({ login: 'ada@example.com' });
    put.mockRejectedValue(Object.assign(new Error('taken'), {
      isAxiosError: true, response: { status: 409, data: { detail: 'That Tailscale login is linked to another account here.' } },
    }));
    render(<TailnetSignIn />);
    fireEvent.click(await screen.findByRole('button', { name: 'Link' }));
    fireEvent.click(screen.getByRole('button', { name: 'Link ada@example.com' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('linked to another account');
  });
});
