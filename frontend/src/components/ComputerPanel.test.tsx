import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ComputerPanel } from './ComputerPanel';

const lib = vi.hoisted(() => ({
  sandboxStatus: vi.fn(),
  openViewer: vi.fn(),
  takeOver: vi.fn(),
  handBack: vi.fn(),
  resetSandbox: vi.fn(),
}));
const rfb = vi.hoisted(() => ({ made: [] as { url: string; password?: string; viewOnly: boolean }[] }));

vi.mock('@/lib/sandbox', async (orig) => ({ ...(await orig<typeof import('@/lib/sandbox')>()), ...lib }));
vi.mock('@novnc/novnc/lib/rfb', () => ({
  default: class extends EventTarget {
    viewOnly = true;
    scaleViewport = false;
    background = '';
    constructor(_el: HTMLElement, public url: string, opts?: { credentials?: { password?: string } }) {
      super();
      rfb.made.push(Object.assign(this, { password: opts?.credentials?.password }));
    }
    disconnect() {}
    focus() {}
  },
}));

const agents = [{ id: 'A1', name: 'Researcher' }, { id: 'b2', name: 'Writer' }];

beforeEach(() => {
  Object.values(lib).forEach((fn) => fn.mockReset());
  rfb.made = [];
  lib.openViewer.mockResolvedValue({ url: 'ws://x/screen?ticket=t', password: 'view' });
  lib.handBack.mockResolvedValue(undefined);
});

describe('ComputerPanel', () => {
  it('explains how to turn it on when it is off', async () => {
    lib.sandboxStatus.mockResolvedValue({ enabled: false, running: false, desktops: [] });
    render(<ComputerPanel agents={agents} onClose={() => {}} />);
    expect(await screen.findByText(/sandboxed computer is off/)).toBeInTheDocument();
  });

  it("shows each agent's desktop, view-only, and takes over and hands back", async () => {
    lib.sandboxStatus.mockResolvedValue({
      enabled: true, running: true,
      desktops: [{ name: 'a1', display: ':1', held_by: null, running: true }, { name: 'b2', display: ':2', held_by: null, running: true }],
    });
    lib.takeOver.mockResolvedValue('full');
    render(<ComputerPanel agents={agents} focusAgentId="b2" onClose={() => {}} />);
    expect(await screen.findByRole('tab', { name: 'Researcher' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Writer' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(rfb.made.length).toBe(1));
    expect(rfb.made[0]).toMatchObject({ password: 'view', viewOnly: true });

    fireEvent.click(screen.getByRole('button', { name: 'Take over' }));
    await waitFor(() => expect(lib.takeOver).toHaveBeenCalledWith('b2'));
    expect(await screen.findByText(/You're in control/)).toBeInTheDocument();
    await waitFor(() => expect(rfb.made[rfb.made.length - 1]).toMatchObject({ password: 'full', viewOnly: false }));

    fireEvent.click(screen.getByRole('button', { name: 'Hand back' }));
    await waitFor(() => expect(lib.handBack).toHaveBeenCalledWith('b2'));
    expect(await screen.findByRole('button', { name: 'Take over' })).toBeInTheDocument();
  });
});
