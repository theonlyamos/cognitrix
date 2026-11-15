import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Knowledge, { type KnowledgeCollection } from './Knowledge';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api,
}));

const docs: KnowledgeCollection = {
  id: 'c1', name: 'Widget docs', description: '',
  documents: [
    {
      id: 'd1', name: 'Widget guide', size_bytes: 2000, status: 'ready', error: null, passages: 12,
      source_url: 'https://docs.example.com/guide/', max_pages: 25, pages_read: 9, refresh_days: 7, refreshed: '2026-11-15 07:00:00',
    },
    { id: 'd2', name: 'leave.md', size_bytes: 2048, status: 'ready', error: null, passages: 2 },
  ],
};

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  api.get.mockResolvedValue({ data: [docs] });
  api.post.mockResolvedValue({ data: {} });
  api.patch.mockResolvedValue({ data: {} });
});

describe('Knowledge web pages', () => {
  it('shows a web source with its pages, address and refresh setting', async () => {
    render(<MemoryRouter><Knowledge /></MemoryRouter>);
    expect(await screen.findByText('Widget guide')).toBeInTheDocument();
    expect(screen.getByText(/9 pages · 12 passages/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /docs\.example\.com\/guide\/ \+ linked pages \(up to 25\)/ })).toBeInTheDocument();
    expect(screen.getByLabelText('How often to read Widget guide again')).toHaveValue('7');
    // Files have no refresh button.
    expect(screen.queryByRole('button', { name: 'Refresh leave.md' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Refresh Widget guide' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/knowledge/c1/documents/d1/refresh'));
    fireEvent.change(screen.getByLabelText('How often to read Widget guide again'), { target: { value: '30' } });
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/knowledge/c1/documents/d1', { refresh_days: 30 }));
  });

  it('adds a page or a site section', async () => {
    render(<MemoryRouter><Knowledge /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Web page address'), { target: { value: 'https://example.com/docs/' } });
    fireEvent.change(screen.getByLabelText('How much of the site to read'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('How often to read it again'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add page' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/knowledge/c1/urls', {
      url: 'https://example.com/docs/', max_pages: 10, refresh_days: 1,
    }));
  });
});
