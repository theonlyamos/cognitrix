import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ComputerPanel } from './ComputerPanel';

const lib = vi.hoisted(() => ({
  sandboxStatus: vi.fn(),
  openViewer: vi.fn(),
  takeOver: vi.fn(),
  handBack: vi.fn(),
  resetSandbox: vi.fn(),
  updateSandbox: vi.fn(),
  startTeaching: vi.fn(),
  stopTeaching: vi.fn(),
  teachingStatus: vi.fn(),
  draftTaughtSkill: vi.fn(),
  saveTaughtSkill: vi.fn(),
  wakeSandbox: vi.fn(),
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
  it('offers to restart onto a newer version only when one is ready', async () => {
    const desktops = [{ name: 'a1', display: ':1', held_by: null, running: true }];
    lib.sandboxStatus.mockResolvedValue({ enabled: true, running: true, desktops, update_available: true });
    lib.updateSandbox.mockResolvedValue(undefined);
    render(<ComputerPanel agents={agents} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Update' }));
    expect(screen.getByText(/files and logins stay/)).toBeInTheDocument();
    lib.sandboxStatus.mockResolvedValue({ enabled: true, running: true, desktops, update_available: false });
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await waitFor(() => expect(lib.updateSandbox).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument());
    expect(lib.resetSandbox).not.toHaveBeenCalled();
  });

  it('says a hosted computer is asleep and wakes it', async () => {
    lib.sandboxStatus.mockResolvedValue({ enabled: true, running: false, desktops: [], host: 'fly', asleep: true });
    lib.wakeSandbox.mockResolvedValue(undefined);
    render(<ComputerPanel agents={agents} focusAgentId="A1" onClose={() => {}} />);
    expect(await screen.findByText(/asleep because nobody used it/)).toBeInTheDocument();
    lib.sandboxStatus.mockResolvedValue({ enabled: true, running: true, desktops: [{ name: 'a1', display: ':1', held_by: null, running: true }], host: 'fly' });
    fireEvent.click(screen.getByRole('button', { name: 'Wake it now' }));
    await waitFor(() => expect(lib.wakeSandbox).toHaveBeenCalledWith('a1'));
    await waitFor(() => expect(rfb.made.length).toBe(1));
  });

  it('says when a hosted computer is on the tailnet', async () => {
    lib.sandboxStatus.mockResolvedValue({
      enabled: true, running: true, host: 'fly', desktops: [],
      tailnet: { name: 'cognitrix-sandbox-abc', dns: 'cognitrix-sandbox-abc.tail1234.ts.net', online: true },
    });
    render(<ComputerPanel agents={agents} onClose={() => {}} />);
    expect(await screen.findByText('On your tailnet')).toHaveAttribute('title', 'cognitrix-sandbox-abc.tail1234.ts.net');
  });

  it("takes over the desktop an agent asked for, once", async () => {
    lib.sandboxStatus.mockResolvedValue({
      enabled: true, running: true,
      desktops: [{ name: 'a1', display: ':1', held_by: null, running: true }, { name: 'b2', display: ':2', held_by: null, running: true }],
    });
    lib.takeOver.mockResolvedValue('full');
    const started = vi.fn();
    render(<ComputerPanel agents={agents} focusAgentId="A1" takeOverRequest={{ desktop: 'b2', at: 1 }} onTakeOverStarted={started} onClose={() => {}} />);
    await waitFor(() => expect(lib.takeOver).toHaveBeenCalledWith('b2'));
    expect(started).toHaveBeenCalled();
    expect(await screen.findByRole('button', { name: 'Hand back' })).toBeInTheDocument();
    expect(lib.takeOver).toHaveBeenCalledTimes(1);
  });

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

  it('teaches a task: records while you show it, then writes and saves a skill', async () => {
    lib.sandboxStatus.mockResolvedValue({
      enabled: true, running: true, desktops: [{ name: 'a1', display: ':1', held_by: null, running: true }],
    });
    const steps = [
      { kind: 'click', app: 'chromium', target: "button 'Invoices'" },
      { kind: 'type', app: 'chromium', target: 'entry', text: 'march' },
    ];
    lib.startTeaching.mockResolvedValue('full');
    lib.teachingStatus.mockResolvedValue({ recording: true, steps: steps.slice(0, 1), text: '' });
    lib.stopTeaching.mockResolvedValue({ steps, text: '' });
    lib.draftTaughtSkill.mockResolvedValue({ name: 'download-invoice', description: 'Download an invoice.', body: '## Steps\n1. Open Invoices' });
    lib.saveTaughtSkill.mockResolvedValue(undefined);
    render(<ComputerPanel agents={agents} focusAgentId="A1" onClose={() => {}} />);
    await waitFor(() => expect(rfb.made.length).toBe(1));

    fireEvent.click(screen.getByRole('button', { name: 'Teach a task' }));
    await waitFor(() => expect(lib.startTeaching).toHaveBeenCalledWith('a1'));
    expect(await screen.findByText(/Recording\. Do the task/)).toBeInTheDocument();
    await waitFor(() => expect(rfb.made[rfb.made.length - 1]).toMatchObject({ password: 'full', viewOnly: false }));
    expect(await screen.findByText(/Click button 'Invoices' in chromium/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(lib.stopTeaching).toHaveBeenCalledWith('a1'));
    const dialog = await screen.findByRole('dialog', { name: 'Teach a task' });
    expect(dialog).toHaveTextContent('Type "march" into entry in chromium');
    fireEvent.change(screen.getByPlaceholderText(/Download this month/), { target: { value: 'Get the invoice' } });
    fireEvent.click(screen.getByRole('button', { name: 'Write the skill' }));
    await waitFor(() => expect(lib.draftTaughtSkill).toHaveBeenCalledWith(
      expect.objectContaining({ steps, title: 'Get the invoice', desktop: 'a1', agentId: 'A1' })));
    fireEvent.change(await screen.findByDisplayValue('Download an invoice.'), { target: { value: 'Get an invoice.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save skill' }));
    await waitFor(() => expect(lib.saveTaughtSkill).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'download-invoice', description: 'Get an invoice.' })));
    expect(await screen.findByText(/Saved as your skill/)).toBeInTheDocument();
    expect(lib.handBack).not.toHaveBeenCalled();
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

  it('leaves Esc to the desktop only while you are in control', async () => {
    lib.sandboxStatus.mockResolvedValue({
      enabled: true, running: true, desktops: [{ name: 'a1', display: ':1', held_by: null, running: true }],
    });
    lib.takeOver.mockResolvedValue('full');
    render(<ComputerPanel agents={agents} focusAgentId="a1" onClose={() => {}} />);
    await waitFor(() => expect(rfb.made.length).toBe(1));
    const panel = screen.getByRole('region', { name: "Agents' computer" });
    const screenEl = screen.getByLabelText("Researcher's screen");

    // Watching: Esc from the (focused) screen still collapses.
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    fireEvent.keyDown(screenEl, { key: 'Escape' });
    expect(panel).toHaveAttribute('data-view', 'docked');

    // In control: Esc belongs to the desktop.
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    fireEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(await screen.findByRole('button', { name: 'Hand back' })).toBeInTheDocument();
    fireEvent.keyDown(screenEl, { key: 'Escape' });
    expect(panel).toHaveAttribute('data-view', 'expanded');
  });
});
