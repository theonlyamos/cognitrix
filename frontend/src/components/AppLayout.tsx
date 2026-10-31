import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import Sidebar from '@/components/Sidebar';
import TitleBar from '@/components/TitleBar';
import CommandPalette from '@/components/CommandPalette';
import AppAlerts from '@/components/AppAlerts';
import { NotificationsProvider } from '@/context/NotificationsContext';
import { isDesktop, onDesktopEvent } from '@/lib/desktop';

/** Persistent shell: title bar, Sidebar and alerts mount once; pages swap
 *  through the Outlet. */
export default function AppLayout() {
  return (
    <NotificationsProvider>
      <AppShell />
    </NotificationsProvider>
  );
}

function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const { pathname } = useLocation();
  const navigate = useNavigate();

  // Ctrl K opens the command palette everywhere; Ctrl N starts a new chat.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
      const key = event.key.toLowerCase();
      if (key === 'k') {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      } else if (key === 'n' && isDesktop()) {
        event.preventDefault();
        navigate('/home');
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [navigate]);

  // The tray menu and quick ask ask the main window to open a page.
  useEffect(() => {
    let stop: (() => void) | undefined;
    void onDesktopEvent<string>('navigate', (path) => {
      if (path === '/home' && window.location.pathname === '/home') window.location.reload();
      else navigate(path);
    }).then((fn) => (stop = fn));
    return () => stop?.();
  }, [navigate]);

  useEffect(() => {
    document.documentElement.classList.add('app-scroll-locked');
    return () => document.documentElement.classList.remove('app-scroll-locked');
  }, []);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!mobileNavOpen) return;

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileNavOpen(false);
    };

    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [mobileNavOpen]);

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-bg text-fg">
      <TitleBar onOpenPalette={() => setPaletteOpen(true)} />
      <AppAlerts />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <div className="flex min-h-0 flex-1 overflow-hidden">
      <button
        type="button"
        aria-label="Open navigation"
        aria-controls="primary-navigation"
        aria-expanded={mobileNavOpen}
        onClick={() => setMobileNavOpen(true)}
        className="fixed left-2 top-2 z-40 grid h-11 w-11 place-items-center rounded border border-line bg-panel text-fg md:hidden"
      >
        <svg
          aria-hidden="true"
          className="h-5 w-5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        >
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      </button>
      {mobileNavOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 z-40 bg-bg/70 md:hidden"
          onClick={() => setMobileNavOpen(false)}
        />
      )}
      <Sidebar mobileOpen={mobileNavOpen} onNavigate={() => setMobileNavOpen(false)} />
      <main id="main-content" className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Outlet />
      </main>
      </div>
    </div>
  );
}
