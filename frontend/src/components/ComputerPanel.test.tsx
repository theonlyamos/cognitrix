import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
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

  it('expands to fill the window, goes full screen, and collapses with Esc', async () => {
    lib.sandboxStatus.mockResolvedValue({
      enabled: true, running: true,
      desktops: [{ name: 'a1', display: ':1', held_by: null, running: true }, { name: 'b2', display: ':2', held_by: null, running: true }],
    });
    render(<ComputerPanel agents={agents} focusAgentId="a1" onClose={() => {}} />);
    await waitFor(() => expect(rfb.made.length).toBe(1));
    const panel = screen.getByRole('region', { name: "Agents' computer" });
    expect(panel).toHaveAttribute('data-view', 'docked');

    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    expect(panel).toHaveAttribute('data-view', 'expanded');
    // One bar: the desktop tabs, Take over and Full screen sit together, and
    // the same live screen stays connected.
    expect(screen.getByRole('tab', { name: 'Writer' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    expect(rfb.made.length).toBe(1);

    const requestFullscreen = vi.fn(async function (this: HTMLElement) {
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: this });
      document.dispatchEvent(new Event('fullscreenchange'));
    });
    panel.requestFullscreen = requestFullscreen;
    fireEvent.click(screen.getByRole('button', { name: 'Full screen' }));
    await waitFor(() => expect(panel).toHaveAttribute('data-view', 'fullscreen'));
    expect(screen.getByRole('button', { name: 'Exit full screen' })).toBeInTheDocument();
    act(() => {
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null });
      document.dispatchEvent(new Event('fullscreenchange'));
    });
    await waitFor(() => expect(panel).toHaveAttribute('data-view', 'expanded'));

    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(panel).toHaveAttribute('data-view', 'docked');
    expect(screen.getByRole('button', { name: 'Expand' })).toBeInTheDocument();
    expect(rfb.made.length).toBe(1);
  });
});
