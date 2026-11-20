import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import ComputerChip from '@/components/ComputerChip';
import type { PairedDevice } from '@/lib/devices';

const pc = (over: Partial<PairedDevice> = {}): PairedDevice => ({
  id: 'd1', name: 'Work laptop', platform: 'Windows-11', online: true, paused: false,
  folders: [{ path: 'C:\\code', writable: true }], powers: ['files', 'commands'], ...over,
});

describe('ComputerChip', () => {
  it('shows the sandbox until a PC is picked, and picks one', () => {
    const onChange = vi.fn();
    render(<ComputerChip devices={[pc(), pc({ id: 'd2', name: 'Home PC', online: false })]} value={null} onChange={onChange} usualIsSandbox onPair={() => {}} />);
    const chip = screen.getByRole('button', { name: 'Computer: Sandbox' });
    fireEvent.click(chip);
    expect(screen.getByRole('menuitemradio', { name: /Sandbox/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('menuitemradio', { name: /Home PC.*Offline/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Work laptop/ }));
    expect(onChange).toHaveBeenCalledWith('d1');
  });

  it('names the picked PC and goes back to the usual computer', () => {
    const onChange = vi.fn();
    render(<ComputerChip devices={[pc()]} value="d1" onChange={onChange} usualIsSandbox onPair={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Computer: Work laptop' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Sandbox/ }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('offers pairing a computer', () => {
    const onPair = vi.fn();
    render(<ComputerChip devices={[pc()]} value={null} onChange={() => {}} usualIsSandbox={false} onPair={onPair} />);
    fireEvent.click(screen.getByRole('button', { name: 'Computer: Usual computer' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Pair a computer/ }));
    expect(onPair).toHaveBeenCalled();
  });
});
