import { render, screen, within } from '@testing-library/react';
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

  it('keeps the closed mobile drawer non-interactive with a desktop visibility override', async () => {
    renderShell();
    const navigation = screen.getByRole('complementary', { name: 'Primary navigation' });

    expect(navigation).toHaveClass('invisible', '-translate-x-full', 'md:visible');

    await userEvent.click(screen.getByRole('button', { name: 'Open navigation' }));

    expect(navigation).toHaveClass('visible', 'translate-x-0', 'md:visible');
    expect(navigation).not.toHaveClass('invisible');
  });

  it('does not expose an inactive search control', () => {
    renderShell();

    expect(screen.queryByRole('button', { name: /Search & run/i })).not.toBeInTheDocument();
  });

  it('uses 44px mobile touch targets with compact desktop overrides', () => {
    renderShell();
    const navigation = screen.getByRole('complementary', { name: 'Primary navigation' });

    expect(screen.getByRole('button', { name: 'Open navigation' })).toHaveClass('h-11', 'w-11');
    for (const link of within(navigation).getAllByRole('link')) {
      expect(link).toHaveClass('min-h-11', 'md:min-h-0');
    }
    expect(within(navigation).getByRole('button', { name: /Switch to .* mode/i })).toHaveClass(
      'h-11',
      'w-11',
      'md:h-9',
      'md:w-9',
    );
    expect(within(navigation).getByRole('button', { name: 'SIGN OUT' })).toHaveClass(
      'h-11',
      'md:h-9',
    );
  });

  it('uses an opaque focus-visible outline on shell controls', async () => {
    renderShell();
    const navigation = screen.getByRole('complementary', { name: 'Primary navigation' });
    const focusClasses = [
      'focus-visible:outline',
      'focus-visible:outline-2',
      'focus-visible:outline-fg',
    ];

    expect(screen.getByRole('button', { name: 'Open navigation' })).toHaveClass(...focusClasses);
    for (const control of [
      ...within(navigation).getAllByRole('link'),
      ...within(navigation).getAllByRole('button'),
    ]) {
      expect(control).toHaveClass(...focusClasses);
    }

    await userEvent.click(screen.getByRole('button', { name: 'Open navigation' }));

    expect(screen.getByRole('button', { name: 'Close navigation' })).toHaveClass(...focusClasses);
  });

  it('renders authenticated content in a named main landmark', () => {
    renderShell();

    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content');
  });
});
