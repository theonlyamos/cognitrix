import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import Sidebar from '@/components/Sidebar';
import { ThemeProvider } from '@/context/ThemeContext';

vi.mock('@/context/AppContext', () => ({
  useUser: () => ({ user: { id: 'u1', name: 'Test User', email: 'test@example.com' }, logout: vi.fn() }),
}));

describe('Sidebar stacking', () => {
  // A transform on the wide-screen sidebar makes it a stacking context, and
  // page content (the chat transcript) then covers the collapsed rail's chat
  // flyout and takes its clicks. The drawer transform is for phones only.
  it('has no transform on wide screens', () => {
    localStorage.setItem('sidebar.collapsed', '1');
    const { container } = render(
      <MemoryRouter initialEntries={['/home']} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
        <ThemeProvider><Sidebar /></ThemeProvider>
      </MemoryRouter>,
    );
    const classes = container.querySelector('#primary-navigation')!.className.split(/\s+/);
    expect(classes).toContain('md:transform-none');
    expect(classes.filter((c) => c.startsWith('md:') && /translate|rotate|scale|skew/.test(c))).toEqual([]);
  });
});
