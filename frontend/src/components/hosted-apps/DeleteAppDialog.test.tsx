import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DeleteAppDialog } from './DeleteAppDialog';

describe('DeleteAppDialog', () => {
  it('deletes only after the person confirms', async () => {
    const onConfirm = vi.fn(() => Promise.resolve());
    const onCancel = vi.fn();
    render(<DeleteAppDialog title="Support log" folder="/home/me/.cognitrix/apps/support-log" onCancel={onCancel} onConfirm={onConfirm} />);

    expect(screen.getByRole('alertdialog', { name: 'Delete “Support log”?' })).toBeInTheDocument();
    expect(screen.getByText(/can’t be undone/)).toBeInTheDocument();
    expect(screen.getByText('/home/me/.cognitrix/apps/support-log')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Delete app/ }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('Cancel and Esc close it without deleting', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<DeleteAppDialog title="Support log" onCancel={onCancel} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
