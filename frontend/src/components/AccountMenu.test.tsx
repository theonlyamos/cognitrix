import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Sidebar from '@/components/Sidebar';
import { ThemeProvider } from '@/context/ThemeContext';

const logout = vi.hoisted(() => vi.fn());
vi.mock('@/context/AppContext', () => ({
  useUser: () => ({ user: { id: 'u1', name: 'Test User', email: 'test@example.com' }, logout }),
}));

function renderSidebar(path = '/home') {
  return render(
    <MemoryRouter initialEntries={[path]} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <ThemeProvider>
        <Sidebar />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('AccountMenu', () => {
  beforeEach(() => {
    logout.mockReset();
    localStorage.clear();
  });

  it('moves Settings and API Keys out of the nav into the account menu', async () => {
    const user = userEvent.setup();
    renderSidebar();
    expect(screen.queryByRole('link', { name: 'Settings' })).toBeNull();
    expect(screen.queryByRole('menu')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    expect(screen.getByRole('menuitem', { name: 'Settings' })).toHaveAttribute('href', '/settings');
    expect(screen.getByRole('menuitem', { name: 'API Keys' })).toHaveAttribute('href', '/api-keys');
  });

  it("opens the What's new window", async () => {
    const user = userEvent.setup();
    const opened = vi.fn();
    window.addEventListener('cognitrix:open-whats-new', opened);
    renderSidebar();
    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    await user.click(screen.getByRole('menuitem', { name: "What's new" }));
    expect(opened).toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
    window.removeEventListener('cognitrix:open-whats-new', opened);
  });

  it('switches the theme and keeps the menu open', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    await user.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(screen.getByRole('radio', { name: 'Dark' })).toHaveAttribute('aria-checked', 'true');
    expect(document.documentElement).toHaveClass('dark');
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('signs out from the menu and closes on Escape', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    await user.click(screen.getByRole('menuitem', { name: 'Sign out' }));
    expect(logout).toHaveBeenCalled();
  });
});
