import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ComputerAppsCard } from './ComputerAppsCard';

const lib = vi.hoisted(() => ({ listComputerApps: vi.fn(), setComputerApp: vi.fn() }));

vi.mock('@/lib/computerApps', () => lib);

const notepad = { app: 'notepad.exe', label: 'Notepad', level: 'allow' as const };

beforeEach(() => {
  Object.values(lib).forEach((fn) => fn.mockReset());
  lib.listComputerApps.mockResolvedValue({ available: true, apps: [notepad] });
});

describe('ComputerAppsCard', () => {
  it('lists answered apps and changes one back to asking', async () => {
    lib.setComputerApp.mockResolvedValue([]);
    render(<ComputerAppsCard />);
    expect(await screen.findByText('Notepad')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Always allow' })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('radio', { name: 'Ask' }));
    await waitFor(() => expect(lib.setComputerApp).toHaveBeenCalledWith('notepad.exe', 'ask'));
    expect(await screen.findByText(/No apps yet/)).toBeInTheDocument();
  });

  it('adds an app by program name', async () => {
    lib.listComputerApps.mockResolvedValue({ available: false, apps: [] });
    lib.setComputerApp.mockResolvedValue([{ app: 'calc.exe', label: 'Calc', level: 'allow' }]);
    render(<ComputerAppsCard />);
    expect(await screen.findByText(/isn't available on the computer/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Program name'), { target: { value: ' calc.exe ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Always allow' }));
    await waitFor(() => expect(lib.setComputerApp).toHaveBeenCalledWith('calc.exe', 'allow'));
    expect(await screen.findByText('Calc')).toBeInTheDocument();
  });
});
