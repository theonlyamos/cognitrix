import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiGet, apiPut } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPut: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: { get: apiGet, put: apiPut } }));
const { who } = vi.hoisted(() => ({ who: { id: 'u1' } }));
vi.mock('@/context/AppContext', () => ({ useOptionalUser: () => ({ id: who.id, name: '', email: '' }) }));

import AutoApproveConfirm from './AutoApproveConfirm';
import { resetAutoApprove, setAutoApprove, useAutoApprove } from '@/lib/autoApprove';

function Probe() {
  const { on } = useAutoApprove();
  return <span>{on ? 'on' : 'off'}</span>;
}

describe('Auto-approve everything', () => {
  beforeEach(() => {
    resetAutoApprove();
    apiGet.mockReset();
    apiPut.mockReset();
  });

  it('warns how dangerous it is before turning it on', async () => {
    const onConfirm = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    render(<AutoApproveConfirm onConfirm={onConfirm} onClose={onClose} />);
    const dialog = screen.getByRole('alertdialog', { name: 'Approve everything without asking?' });
    expect(dialog).toHaveTextContent(/DANGEROUS/);
    expect(dialog).toHaveTextContent(/can't be undone/);
    // Cancel has focus, so Enter doesn't turn it on by accident.
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await userEvent.click(screen.getByRole('button', { name: 'Turn on auto-approve' }));
    expect(onConfirm).toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('is read from and saved to the account, not the browser', async () => {
    localStorage.setItem('bypassPermissions', '1');
    apiGet.mockResolvedValue({ data: { on: false } });
    apiPut.mockResolvedValue({ data: { on: true } });
    render(<Probe />);
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/account/auto-approve'));
    expect(screen.getByText('off')).toBeInTheDocument();
    expect(localStorage.getItem('bypassPermissions')).toBeNull();
    await setAutoApprove(true);
    expect(apiPut).toHaveBeenCalledWith('/account/auto-approve', { on: true });
    await waitFor(() => expect(screen.getByText('on')).toBeInTheDocument());
  });

  it('fetches the new account\'s setting after signing in as someone else', async () => {
    apiGet.mockResolvedValueOnce({ data: { on: false } }).mockResolvedValueOnce({ data: { on: true } });
    who.id = 'u1';
    const { rerender } = render(<Probe />);
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));
    expect(screen.getByText('off')).toBeInTheDocument();
    who.id = 'u2';
    rerender(<Probe key="u2" />);
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText('on')).toBeInTheDocument());
  });
});
