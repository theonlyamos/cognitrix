import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import Sidebar from '@/components/Sidebar';
import TitleBar from '@/components/TitleBar';
import CommandPalette from '@/components/CommandPalette';
import AppAlerts from '@/components/AppAlerts';
import { FileViewerHost } from '@/components/files/FileViewer';
import AssistantSetup from '@/components/AssistantSetup';
import WhatsNew from '@/components/WhatsNew';
import { NotificationsProvider } from '@/context/NotificationsContext';
import { isDesktop, onDesktopEvent, invokeDesktop } from '@/lib/desktop';
import { tr } from '@/lib/i18n';
import { CHILL_PATH, getUiMode, isLandingPath, isModeSwitchKey } from '@/lib/uiMode';

/** Persistent shell: the sidebar (with every chat) and alerts mount once;
 *  pages swap through the Outlet. */
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
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const chill = pathname === CHILL_PATH;

  // The app opens in chill mode when that's where the person left it.
  const [startPath] = useState(() => ({ pathname, search }));
  useEffect(() => {
    if (getUiMode() === 'chill' && isLandingPath(startPath.pathname, startPath.search)) navigate(CHILL_PATH, { replace: true });
  }, [navigate, startPath]);

  // Ctrl+Shift+. switches to chill mode from any page. The chat screens
  // handle it themselves so the open chat comes along.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isModeSwitchKey(event) || pathname === '/home' || pathname === CHILL_PATH) return;
      event.preventDefault();
      navigate(CHILL_PATH);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [navigate, pathname]);

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
    // A cognitrix:// link that opened the app before this page was up.
    void invokeDesktop<string | null>('take_pending_link').then((path) => {
      if (path && path.startsWith('/')) navigate(path);
    }).catch(() => {});
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
      <TitleBar />
      <AppAlerts />
      <FileViewerHost />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <AssistantSetup />
      <WhatsNew />
      <div className="flex min-h-0 flex-1 overflow-hidden">
      {!chill && (<>
      <button
        type="button"
        aria-label={tr('ui.appLayout.openNavigation')}
        aria-controls="primary-navigation"
        aria-expanded={mobileNavOpen}
        onClick={() => setMobileNavOpen(true)}
        className="fixed left-2 top-1.5 z-40 grid h-11 w-11 place-items-center rounded-lg text-fg md:hidden"
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
          aria-label={tr('ui.appLayout.closeNavigation')}
          className="fixed inset-0 z-40 bg-bg/70 md:hidden"
          onClick={() => setMobileNavOpen(false)}
        />
      )}
      <Sidebar mobileOpen={mobileNavOpen} onNavigate={() => setMobileNavOpen(false)} onOpenSearch={() => setPaletteOpen(true)} />
      </>)}
      <main id="main-content" className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Outlet />
      </main>
      </div>
    </div>
  );
}
