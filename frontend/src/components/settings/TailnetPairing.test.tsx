import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ComputersSettings } from './Computers';
import { ThisPcLink } from './ThisPcLink';

const { get, post, del, patch } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), del: vi.fn(), patch: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, post, delete: del, patch },
}));
vi.mock('@/lib/folders', () => ({ pickFolder: vi.fn().mockResolvedValue('C:\\code\\app') }));
vi.mock('@/components/settings/SentTasks', () => ({ SentTasks: () => null }));

const request = { id: 'r1', name: 'Work laptop', platform: 'Windows 11', login: 'kofi@example.com', node: 'work-laptop', address: '100.64.0.9', client: 'desktop', expires_in: 590 };
const link = (over = {}) => ({
  linked: false, server: '', account: '', device_id: '', name: '', folders: [], powers: [], paused: false, status: 'unpaired',
  error: null, connected_since: null, default_name: 'work-laptop', platform: 'Windows 11', ...over,
});

beforeEach(() => { [get, post, del, patch].forEach((fn) => fn.mockReset()); });
afterEach(() => { vi.useRealTimers(); });

describe('Settings › Computers on the web', () => {
  it('shows a PC from my tailnet that asked to pair, and approves it', async () => {
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/computer/devices/pair-requests' ? { requests: [request] } : { devices: [] },
    }));
    post.mockResolvedValue({ data: { requests: [] } });
    render(<MemoryRouter><ComputersSettings /></MemoryRouter>);
    const card = await screen.findByRole('group', { name: 'Work laptop wants to pair' });
    expect(card).toHaveTextContent('Seen on your tailnet as work-laptop, signed in to Tailscale as kofi@example.com');
    expect(card).toHaveTextContent('Approve only if you just asked for this on that PC');
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/computer/devices/pair-requests/r1/approve'));
    expect(await screen.findByText(/Approved. Now finish on Work laptop/)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Work laptop wants to pair' })).toBeNull();
  });

  it('declines one, and says why an approval was refused', async () => {
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/computer/devices/pair-requests' ? { requests: [request] } : { devices: [] },
    }));
    post.mockRejectedValueOnce(Object.assign(new Error('too new'), {
      isAxiosError: true, response: { status: 403, data: { detail: "This device signed in with a code less than a day ago, so it can't approve a PC yet." } },
    }));
    render(<MemoryRouter><ComputersSettings /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("can't approve a PC yet");
    post.mockResolvedValue({ data: { requests: [] } });
    fireEvent.click(screen.getByRole('button', { name: 'Decline' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/computer/devices/pair-requests/r1/deny'));
  });

  it('says which tailnet machine a paired PC is', async () => {
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/computer/devices/pair-requests' ? { requests: [] }
        : { devices: [{ id: 'd1', name: 'Work laptop', platform: 'Windows 11', online: true, paused: false, folders: [], powers: ['files'], tailnet_node: 'work-laptop' }] },
    }));
    render(<MemoryRouter><ComputersSettings /></MemoryRouter>);
    expect(await screen.findByText(/on your tailnet as work-laptop/)).toBeInTheDocument();
  });
});

describe('Desktop app › This PC', () => {
  const found = [
    { name: 'studio', server: 'https://studio.tail1234.ts.net', version: '0.7.0', ask_to_pair: true },
    { name: 'old-box', server: 'http://old-box.tail1234.ts.net:8000', version: '0.6.7', ask_to_pair: false },
  ];

  it('finds the server on the tailnet and fills in its address', async () => {
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/computer/link' ? link() : url === '/computer/link/find' ? { servers: found } : { status: 'none', server: '', account: '' },
    }));
    render(<MemoryRouter><ThisPcLink /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Find it on my tailnet (Tailscale)' }));
    const list = await screen.findByRole('list', { name: 'Servers on your tailnet' });
    expect(list).toHaveTextContent('studio');
    // Only a server that knows this PC's Tailscale login offers pairing without a code.
    expect(screen.getAllByRole('button', { name: 'Pair without a code' })).toHaveLength(1);
    fireEvent.click(screen.getAllByRole('button', { name: 'Use this address' })[1]);
    expect(screen.getByLabelText('Web address')).toHaveValue('http://old-box.tail1234.ts.net:8000');
  });

  it('asks to pair, waits for the yes on the web, then shares folders with no code', async () => {
    let asked = { status: 'none', server: '', account: '' };
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/computer/link' ? link() : url === '/computer/link/find' ? { servers: found } : asked,
    }));
    post.mockImplementation((url: string) => {
      if (url === '/computer/link/ask') {
        asked = { status: 'pending', server: 'https://studio.tail1234.ts.net', account: 'Kofi Boateng · kofi@example.com' };
        return Promise.resolve({ data: asked });
      }
      return Promise.resolve({ data: link({ linked: true, server: asked.server, account: asked.account, name: 'work-laptop', status: 'connected', folders: [{ path: 'C:\\code\\app', writable: true }], powers: ['files', 'commands'] }) });
    });
    render(<MemoryRouter><ThisPcLink /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Find it on my tailnet (Tailscale)' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Pair without a code' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/computer/link/ask', { server: 'https://studio.tail1234.ts.net', name: 'work-laptop' }));
    expect(await screen.findByText('Approve this PC on the web')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Kofi Boateng · kofi@example.com');

    asked = { ...asked, status: 'approved' };
    expect(await screen.findByText(/Let Kofi Boateng · kofi@example.com use this PC/, undefined, { timeout: 5000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add a folder' }));
    await screen.findByText('C:\\code\\app');
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/computer/link/ask/finish', {
      name: 'work-laptop', folders: [{ path: 'C:\\code\\app', writable: true }], powers: ['files', 'commands'],
    }));
    expect(await screen.findByRole('button', { name: 'Unlink this PC' })).toBeInTheDocument();
  }, 15000);

  it('says so when it was declined, and when Tailscale is not running here', async () => {
    let asked = { status: 'pending', server: 'https://studio.tail1234.ts.net', account: 'Kofi' };
    let findFails = false;
    get.mockImplementation((url: string) => {
      if (url === '/computer/link/find' && findFails) {
        return Promise.reject(Object.assign(new Error('no'), { isAxiosError: true, response: { status: 400, data: { detail: "Tailscale isn't running on this PC, or isn't signed in." } } }));
      }
      return Promise.resolve({ data: url === '/computer/link' ? link() : url === '/computer/link/find' ? { servers: [] } : asked });
    });
    render(<MemoryRouter><ThisPcLink /></MemoryRouter>);
    // A request from before this screen was opened is picked up again.
    expect(await screen.findByText('Approve this PC on the web')).toBeInTheDocument();
    asked = { status: 'denied', server: '', account: '' };
    expect(await screen.findByText('It was declined on the web.', undefined, { timeout: 5000 })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Find it on my tailnet (Tailscale)' }));
    expect(await screen.findByText(/No Cognitrix server answered on your tailnet/)).toBeInTheDocument();
    findFails = true;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Find it on my tailnet (Tailscale)' })); });
    expect(await screen.findByRole('alert')).toHaveTextContent("Tailscale isn't running on this PC");
  }, 15000);
});
