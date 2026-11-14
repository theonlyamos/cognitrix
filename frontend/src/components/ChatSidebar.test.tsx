import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ChatSidebar, loadSidebarOpen, saveSidebarOpen, type ChatSidebarProps } from './ChatSidebar';
import type { PageSummary } from '@/lib/artifactPages';

vi.mock('@/components/ComputerPreview', () => ({
  ComputerPreview: ({ onOpen, label }: { onOpen: () => void; label: string }) => (
    <button type="button" onClick={onOpen}>{`Open ${label}'s computer`}</button>
  ),
}));
vi.mock('@/components/ArtifactPreview', () => ({ ArtifactPreview: () => <div data-testid="image" /> }));
vi.mock('@/components/FolderChips', () => ({ default: () => <div data-testid="folders" /> }));

const page = (id: string, title: string): PageSummary => ({
  id, title, format: 'html', version: 2, viewer_version: 2, size_bytes: 10, role: 'owner',
  owner: { id: 'u', name: 'Me' }, agent: null, session_id: 's1', task_id: '', live: false, access: 'private',
  pinned: false, powers: [], open_comments: 0, created: '2026-11-13 10:00:00', updated: '2026-11-13 10:00:00',
});

function setup(overrides: Partial<ChatSidebarProps> = {}) {
  const props: ChatSidebarProps = {
    onClose: vi.fn(),
    desktop: 'a1',
    sandboxOn: true,
    onOpenComputer: vi.fn(),
    waiting: [{ id: 'r1', kind: 'approval', title: 'Run Bash?', detail: 'rm build/' }],
    onApprove: vi.fn(),
    onDeny: vi.fn(),
    onShowWaiting: vi.fn(),
    pages: [page('p1', 'Lisbon itinerary')],
    newPageIds: new Set(['p1']),
    onOpenArtifact: vi.fn(),
    images: [],
    files: [],
    activity: [{ key: 'k1', name: 'Web Search', status: 'done' }, { key: 'k2', name: 'Publish Artifact', status: 'running' }],
    busy: true,
    folders: [],
    onChangeFolders: vi.fn(),
    about: { agentId: 'a1', agentName: 'Assistant', model: 'anthropic · claude', messages: 4 },
    ...overrides,
  };
  render(<MemoryRouter><ChatSidebar {...props} /></MemoryRouter>);
  return props;
}

describe('ChatSidebar', () => {
  beforeEach(() => localStorage.clear());

  it('opens the computer from its live preview and artifacts from their rows', async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole('button', { name: "Open Assistant's computer" }));
    expect(props.onOpenComputer).toHaveBeenCalled();
    const artifacts = screen.getByRole('region', { name: 'Artifacts' });
    expect(within(artifacts).getByText('NEW')).toBeInTheDocument();
    await user.click(within(artifacts).getByRole('button', { name: /Lisbon itinerary/ }));
    expect(props.onOpenArtifact).toHaveBeenCalledWith('p1');
  });

  it('answers an approval from the Waiting on you section', async () => {
    const user = userEvent.setup();
    const props = setup();
    const waiting = screen.getByRole('region', { name: 'Waiting on you' });
    await user.click(within(waiting).getByRole('button', { name: 'Approve' }));
    expect(props.onApprove).toHaveBeenCalledWith('r1');
    await user.click(within(waiting).getByRole('button', { name: 'Deny' }));
    expect(props.onDeny).toHaveBeenCalledWith('r1');
  });

  it('says so when the computer is idle or off', () => {
    setup({ desktop: null });
    expect(screen.getByText(/isn't using its computer right now/)).toBeInTheDocument();
  });

  it('folds sections and hides them from the sections menu, remembered per browser', async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole('button', { name: /Activity/ }));
    expect(screen.queryByText('Web Search')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Choose sections' }));
    await user.click(screen.getByRole('checkbox', { name: 'Folders' }));
    expect(screen.queryByRole('region', { name: 'Folders' })).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem('chatSidebar.closed') || '[]')).toEqual(['activity']);
    expect(JSON.parse(localStorage.getItem('chatSidebar.hidden') || '[]')).toEqual(['folders']);
  });

  it('remembers open or closed, and knows when the person never chose', () => {
    expect(loadSidebarOpen()).toBeNull();
    saveSidebarOpen(true);
    expect(loadSidebarOpen()).toBe(true);
    saveSidebarOpen(false);
    expect(loadSidebarOpen()).toBe(false);
  });
});
