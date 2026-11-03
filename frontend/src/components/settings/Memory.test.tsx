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
  memories, settings: { enabled: true, auto_save: 'auto' }, limit: 500, max_chars: 300,
});

beforeEach(() => {
  Object.values(lib).forEach((fn) => fn.mockReset());
  lib.listMemories.mockResolvedValue(list([short, pnpm]));
  lib.legacyMemoryCount.mockResolvedValue(0);
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
    expect(screen.getByText('GLOBAL')).toBeInTheDocument();
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
    lib.saveMemorySettings.mockResolvedValue({ enabled: false, auto_save: 'auto' });
    lib.deleteAllMemories.mockResolvedValue(2);
    renderSettings();
    fireEvent.click(await screen.findByLabelText('Use saved memories'));
    await waitFor(() => expect(lib.saveMemorySettings).toHaveBeenCalledWith({ enabled: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete all memories' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete all' }));
    await waitFor(() => expect(lib.deleteAllMemories).toHaveBeenCalled());
  });
});
