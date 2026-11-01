import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import FolderChips from './FolderChips';
import type { WorkFolder } from '@/lib/folders';

vi.mock('@/lib/folders', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/folders')>();
  return {
    ...real,
    checkFolder: vi.fn(async (path: string, access: 'read' | 'write') => ({ path, access, name: 'docs', primary: false })),
  };
});

const FOLDERS: WorkFolder[] = [
  { path: '/work/app', access: 'write', name: 'app', primary: true },
  { path: '/notes', access: 'read', name: 'notes', primary: false },
];

describe('FolderChips', () => {
  it('switches a folder to read & write and back', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FolderChips folders={FOLDERS} onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: /notes/ }));
    await user.click(screen.getByRole('menuitem', { name: /Allow writing/ }));
    expect(onChange).toHaveBeenCalledWith([
      FOLDERS[0],
      { ...FOLDERS[1], access: 'write' },
    ]);
  });

  it('makes another folder primary and puts it first', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FolderChips folders={FOLDERS} onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: /notes/ }));
    await user.click(screen.getByRole('menuitem', { name: /Make primary/ }));
    const next = onChange.mock.calls[0][0] as WorkFolder[];
    expect(next.map((f) => [f.name, f.primary])).toEqual([['notes', true], ['app', false]]);
  });

  it('adds a typed folder as read only by default', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FolderChips folders={FOLDERS} onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: '+ folder' }));
    await user.type(screen.getByLabelText('Folder path'), '/docs');
    await user.click(screen.getByRole('button', { name: 'Add' }));
    const next = onChange.mock.calls[0][0] as WorkFolder[];
    expect(next[2]).toMatchObject({ path: '/docs', access: 'read', primary: false });
  });

  it('shows the empty label when there are no folders', () => {
    render(<FolderChips folders={[]} onChange={vi.fn()} emptyLabel="private scratch folder" />);
    expect(screen.getByText('private scratch folder')).toBeInTheDocument();
  });
});
