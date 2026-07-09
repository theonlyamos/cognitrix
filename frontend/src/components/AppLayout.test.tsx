import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import AppLayout from '@/components/AppLayout';
import { ThemeProvider } from '@/context/ThemeContext';

vi.mock('@/context/AppContext', () => ({
  useUser: () => ({
    user: {
      id: 'user-1',
      name: 'Test User',
      email: 'test@example.com',
    },
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    checkAuth: vi.fn().mockResolvedValue(undefined),
  }),
}));

function renderShell() {
  return render(
    <MemoryRouter
      initialEntries={['/home']}
      future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
    >
      <ThemeProvider>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/home" element={<div>Authenticated home</div>} />
            <Route path="/agents" element={<div>Authenticated agents</div>} />
          </Route>
        </Routes>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('AppLayout', () => {
  it('opens and closes primary navigation on mobile', async () => {
    renderShell();
    const trigger = screen.getByRole('button', { name: 'Open navigation' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    await userEvent.click(trigger);

    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('complementary', { name: 'Primary navigation' })).toHaveAttribute(
      'data-mobile-open',
      'true',
    );

    await userEvent.keyboard('{Escape}');

    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes mobile navigation after following a route', async () => {
    renderShell();
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation' }));

    await userEvent.click(screen.getByRole('link', { name: 'Agents' }));

    expect(screen.getByRole('button', { name: 'Open navigation' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('renders authenticated content in a named main landmark', () => {
    renderShell();

    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content');
  });
});
