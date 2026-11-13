import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Artifacts from './Artifacts';
import { ArtifactCard } from '@/components/artifacts/ArtifactCard';
import { onOpenArtifact, publishedFrom } from '@/lib/artifactPages';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const apiDelete = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get: apiGet, post: apiPost, delete: apiDelete },
}));
vi.mock('@/components/ArtifactPreview', () => ({ ArtifactPreview: ({ artifact }: { artifact: { id: string } }) => <div data-testid={`image-${artifact.id}`} /> }));

const page = (over: Record<string, unknown>) => ({
  id: 'p1', title: 'Weekly numbers', format: 'html', version: 3, viewer_version: 3, size_bytes: 2048, role: 'owner',
  owner: { id: 'u1', name: 'Ada' }, agent: { id: 'a1', name: 'Analyst' }, session_id: 's1', task_id: '', live: false,
  access: 'private', pinned: false, powers: [], open_comments: 0, created: '2026-11-12 10:00:00', updated: '2026-11-12 10:00:00', ...over,
});

const LIST = {
  public_links: true,
  pages: [page({}), page({ id: 'p2', title: 'Launch plan', role: 'view', owner: { id: 'u2', name: 'Grace' }, access: 'people', open_comments: 2 }),
    page({ id: 'p3', title: 'Status board', live: true, access: 'public', task_id: 't1' })],
  images: [{ id: 'i1', kind: 'image', title: 'A red fox', mime_type: 'image/png', session_id: 's1', agent: null, width: 512, height: 512, size_bytes: 9000, created: '2026-11-11 09:00:00', updated: '2026-11-11 09:00:00' }],
  files: [{ id: 'f1', kind: 'file', title: 'notes.md', mime_type: 'text/markdown', path: '/work/notes.md', session_id: 's1', task_id: '', agent: { id: 'a1', name: 'Analyst' }, size_bytes: 900, created: '2026-11-10 09:00:00', updated: '2026-11-10 09:00:00' }],
};

function show() {
  return render(
    <MemoryRouter initialEntries={['/artifacts']}>
      <Routes>
        <Route path="/artifacts" element={<Artifacts />} />
        <Route path="/artifacts/:id" element={<div>one artifact</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Artifacts page', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiDelete.mockReset();
    apiGet.mockImplementation((url: string) => Promise.resolve({ data: url === '/artifact-pages' ? LIST : 'file text' }));
  });

  it('lists pages, images and documents together', async () => {
    show();
    expect(await screen.findByText('Weekly numbers')).toBeInTheDocument();
    expect(screen.getByText('Launch plan')).toBeInTheDocument();
    expect(screen.getByText(/shared by Grace/)).toBeInTheDocument();
    expect(screen.getByTestId('image-i1')).toBeInTheDocument();
    expect(screen.getByText('notes.md')).toBeInTheDocument();
  });

  it('filters by kind, sharing and search', async () => {
    show();
    await screen.findByText('Weekly numbers');
    fireEvent.click(screen.getByRole('tab', { name: 'Documents' }));
    expect(screen.queryByText('Weekly numbers')).not.toBeInTheDocument();
    expect(screen.getByText('notes.md')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Shared with me' }));
    expect(screen.getByText('Launch plan')).toBeInTheDocument();
    expect(screen.queryByText('notes.md')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Live' }));
    expect(screen.getByText('Status board')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'All' }));
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'fox' } });
    expect(screen.getByText('A red fox')).toBeInTheDocument();
    expect(screen.queryByText('Status board')).not.toBeInTheDocument();
  });

  it('turns a document into a page and opens it', async () => {
    apiPost.mockResolvedValue({ data: { id: 'p9' } });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Make a page' }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/artifact-pages/files/f1/publish', {}));
    expect(await screen.findByText('one artifact')).toBeInTheDocument();
  });

  it('opens a text document to read and removes it from the list', async () => {
    apiDelete.mockResolvedValue({ data: { removed: true } });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Open' }));
    expect(await screen.findByRole('dialog', { name: 'notes.md' })).toHaveTextContent('file text');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/artifact-pages/files/f1'));
  });
});

describe('chat artifact card', () => {
  it('reads what a Publish Artifact call published', () => {
    expect(publishedFrom('Publish Artifact', 'Published "Q3 report" (artifact_id ab12, version 1). It is open beside the chat.'))
      .toEqual({ id: 'ab12', title: 'Q3 report', version: 1 });
    expect(publishedFrom('publish_artifact', 'Updated to version 4: "Q3 report" (artifact_id ab12, version 4).')?.version).toBe(4);
    expect(publishedFrom('Write', 'Published "x" (artifact_id ab12, version 1)')).toBeNull();
    expect(publishedFrom('Publish Artifact', 'Error: too big')).toBeNull();
  });

  it('asks the chat to open the artifact beside it', () => {
    const opened: string[] = [];
    const off = onOpenArtifact((id) => opened.push(id));
    render(<MemoryRouter><ArtifactCard toolName="Publish Artifact" result={'Published "Q3 report" (artifact_id ab12, version 2).'} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /Q3 report/ }));
    off();
    expect(opened).toEqual(['ab12']);
  });

  it('goes to the artifact page when no chat is on screen', () => {
    render(
      <MemoryRouter initialEntries={['/x']}>
        <Routes>
          <Route path="/x" element={<ArtifactCard toolName="Publish Artifact" result={'Published "Q3 report" (artifact_id ab12, version 2).'} />} />
          <Route path="/artifacts/:id" element={<div>one artifact</div>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /Q3 report/ }));
    expect(screen.getByText('one artifact')).toBeInTheDocument();
  });
});
