import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MemorySettings } from './Memory';
import { MemoryNotice } from '@/components/MemoryNotice';
import { memoryChange, type MemoryList, type SavedMemory } from '@/lib/memories';

const lib = vi.hoisted(() => ({
  listMemories: vi.fn(),
  addMemory: vi.fn(),
  updateMemory: vi.fn(),
  deleteMemory: vi.fn(),
  deleteAllMemories: vi.fn(),
  saveMemorySettings: vi.fn(),
  legacyMemoryCount: vi.fn(),
  deleteLegacyMemory: vi.fn(),
  approveMemory: vi.fn(),
  settlePendingMemories: vi.fn(),
  importMemories: vi.fn(),
  convertLegacyMemory: vi.fn(),
  exportMemories: vi.fn(),
  dreamStatus: vi.fn(),
  dreamNow: vi.fn(),
  answerDreamProposal: vi.fn(),
  undoDream: vi.fn(),
  getMemoryProfile: vi.fn(),
  saveMemoryProfile: vi.fn(),
  memoryHistory: vi.fn(),
  undoMemoryChange: vi.fn(),
}));

vi.mock('@/lib/memories', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/memories')>()),
  ...lib,
}));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get: vi.fn().mockResolvedValue({ data: [{ id: 'a1', name: 'Coder' }] }) },
}));

const short: SavedMemory = {
  id: 'm1', content: 'Prefers short answers.', scope: 'global', agent_id: null, agent_name: null,
  source: 'agent', source_session_id: 's1', pinned: true, created: '2026-11-01 10:00:00', updated: '2026-11-01 10:00:00',
};
const pnpm: SavedMemory = {
  ...short, id: 'm2', content: 'Uses pnpm for the frontend.', scope: 'agent', agent_id: 'a1', agent_name: 'Coder',
  source: 'user', source_session_id: null, pinned: false,
};

const list = (memories: SavedMemory[]): MemoryList => ({
  memories, settings: { enabled: true, history_enabled: true, auto_save: 'auto' }, limit: 500, max_chars: 300,
});

beforeEach(() => {
  Object.values(lib).forEach((fn) => fn.mockReset());
  lib.listMemories.mockResolvedValue(list([short, pnpm]));
  lib.legacyMemoryCount.mockResolvedValue(0);
  lib.dreamStatus.mockResolvedValue({ enabled: true, running: false, runs: [], proposals: [], hour: 3 });
  lib.getMemoryProfile.mockResolvedValue({ text: '', locked: false, updated: null, by_dream: false });
  lib.memoryHistory.mockResolvedValue([]);
});

const renderSettings = () => render(<MemoryRouter><MemorySettings /></MemoryRouter>);

describe('memoryChange', () => {
  it('reads Memory tool results', () => {
    expect(memoryChange('Memory', 'Memory saved (id 42, global): Likes tea.')).toEqual({
      kind: 'saved', id: '42', scope: 'global', content: 'Likes tea.',
    });
    expect(memoryChange('Memory', 'Memory forgotten (id 42): Likes tea.')?.kind).toBe('forgotten');
    expect(memoryChange('Memory', 'Already remembered (id 42): Likes tea.')).toBeNull();
    expect(memoryChange('Bash', 'Memory saved (id 42, global): x')).toBeNull();
  });
});

describe('MemoryNotice', () => {
  it('shows what was saved and undoes it', async () => {
    lib.deleteMemory.mockResolvedValue(undefined);
    render(<MemoryRouter><MemoryNotice toolName="Memory" result="Memory saved (id 42, global): Likes tea." /></MemoryRouter>);
    expect(screen.getByText('Likes tea.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Manage' })).toHaveAttribute('href', '/settings/memory?memory=42');
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(lib.deleteMemory).toHaveBeenCalledWith('42'));
    expect(await screen.findByText('Memory removed:')).toBeInTheDocument();
  });
});

describe('MemorySettings', () => {
  it('lists memories with their scope and filters by agent', async () => {
    renderSettings();
    expect(await screen.findByText('Prefers short answers.')).toBeInTheDocument();
    expect(screen.getByText('ALL AGENTS')).toBeInTheDocument();
    expect(screen.getByText('CODER')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('radio', { name: 'Coder' }));
    expect(screen.queryByText('Prefers short answers.')).not.toBeInTheDocument();
    expect(screen.getByText('Uses pnpm for the frontend.')).toBeInTheDocument();
  });

  it('adds a memory for one agent', async () => {
    lib.addMemory.mockResolvedValue(pnpm);
    renderSettings();
    fireEvent.click(await screen.findByRole('button', { name: '+ Add' }));
    fireEvent.change(screen.getByLabelText('Memory'), { target: { value: 'Ships on Fridays.' } });
    fireEvent.change(screen.getByLabelText('Who remembers this'), { target: { value: 'a1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lib.addMemory).toHaveBeenCalledWith({ content: 'Ships on Fridays.', scope: 'agent', agent_id: 'a1' }));
  });

  it('turns memory off and deletes everything after confirming', async () => {
    lib.saveMemorySettings.mockResolvedValue({ enabled: false, history_enabled: true, auto_save: 'auto' });
    lib.deleteAllMemories.mockResolvedValue(2);
    renderSettings();
    fireEvent.click(await screen.findByLabelText('Use saved memories'));
    await waitFor(() => expect(lib.saveMemorySettings).toHaveBeenCalledWith({ enabled: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete all memories' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete all' }));
    await waitFor(() => expect(lib.deleteAllMemories).toHaveBeenCalled());
  });
});

describe('memory phase 3', () => {
  it('reads a proposal and ignores the note for the agent', () => {
    expect(memoryChange('Memory', 'Memory proposed (id 7, agent): Lives in Accra.\nYou read outside content this turn.')).toEqual({
      kind: 'proposed', id: '7', scope: 'agent', content: 'Lives in Accra.',
    });
  });

  it('asks before saving a proposed memory', async () => {
    lib.approveMemory.mockResolvedValue({ ...short, id: '7' });
    render(<MemoryRouter><MemoryNotice toolName="Memory" result={'Memory proposed (id 7, global): Lives in Accra.\nWaiting.'} /></MemoryRouter>);
    expect(screen.getByText('Save to memory?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lib.approveMemory).toHaveBeenCalledWith('7'));
    expect(await screen.findByText('Memory saved:')).toBeInTheDocument();
  });

  it('lists memories waiting for an OK apart from saved ones', async () => {
    const waiting: SavedMemory = { ...short, id: 'p1', content: 'Lives in Lisbon.', pinned: false, pending: true, source: 'import' };
    lib.listMemories.mockResolvedValue(list([short, waiting]));
    lib.settlePendingMemories.mockResolvedValue(undefined);
    renderSettings();
    expect(await screen.findByText('WAITING FOR YOUR OK · 1')).toBeInTheDocument();
    expect(screen.getByText('SAVED MEMORIES · 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save all' }));
    await waitFor(() => expect(lib.settlePendingMemories).toHaveBeenCalledWith(true));
  });

  it('imports a paste and says how many wait for review', async () => {
    lib.importMemories.mockResolvedValue({ proposed: 2, duplicates: 0, skipped: [] });
    renderSettings();
    fireEvent.click(await screen.findByRole('button', { name: 'Import…' }));
    fireEvent.change(screen.getByLabelText('Memories to import'), { target: { value: '- Likes tea\n- Lives in Accra' } });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(lib.importMemories).toHaveBeenCalledWith('- Likes tea\n- Lives in Accra'));
    expect(await screen.findByText(/2 memories from your import waiting for your OK/)).toBeInTheDocument();
  });
});

describe('dream mode', () => {
  it('tidies now and shows what it did', async () => {
    lib.dreamNow.mockResolvedValue({ id: 'd1', status: 'running' });
    lib.dreamStatus
      .mockResolvedValueOnce({ enabled: true, running: false, runs: [], proposals: [], hour: 3 })
      .mockResolvedValue({
        enabled: true, running: false, hour: 3, proposals: [],
        runs: [{ id: 'd1', trigger: 'manual', status: 'done', started: '2026-11-16 10:00:00', finished: '2026-11-16 10:01:00',
          summary: 'Merged 2 repeats.', stats: {} }],
      });
    renderSettings();
    fireEvent.click(await screen.findByRole('button', { name: 'Tidy now' }));
    await waitFor(() => expect(lib.dreamNow).toHaveBeenCalled());
    expect(await screen.findByRole('status')).toHaveTextContent('Merged 2 repeats.');
  });

  it('asks before bigger changes and needs a choice for a conflict', async () => {
    lib.dreamStatus.mockResolvedValue({
      enabled: true, running: false, runs: [], hour: 3,
      proposals: [{
        id: 'p1', dream_id: 'd1', action: 'conflict', reason: 'Two homes', status: 'pending', created: '', content: null,
        kind: null, valid_until: null,
        memories: [{ id: 'm1', content: 'Lives in Accra.', source: 'agent' }, { id: 'm2', content: 'Lives in Kumasi.', source: 'agent' }],
      }],
    });
    lib.answerDreamProposal.mockResolvedValue(undefined);
    renderSettings();
    const keep = await screen.findByRole('button', { name: 'Keep this one' });
    expect(keep).toBeDisabled();
    fireEvent.click(screen.getByLabelText('Lives in Kumasi.'));
    fireEvent.click(keep);
    await waitFor(() => expect(lib.answerDreamProposal).toHaveBeenCalledWith('p1', true, 'm2'));
  });

  it('shows the summary about you and locks it', async () => {
    lib.getMemoryProfile.mockResolvedValue({ text: 'Amos builds Cognitrix.', locked: false, updated: null, by_dream: true });
    lib.saveMemoryProfile.mockResolvedValue({ text: 'Amos builds Cognitrix.', locked: true, updated: null, by_dream: false });
    renderSettings();
    expect(await screen.findByText('Amos builds Cognitrix.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Lock' }));
    await waitFor(() => expect(lib.saveMemoryProfile).toHaveBeenCalledWith('Amos builds Cognitrix.', true));
  });
});
