import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { isDesktop, windowAction } from '@/lib/desktop';
import { useOptionalNotifications } from '@/context/NotificationsContext';
import NotificationPopover from '@/components/NotificationPopover';
import { cn } from '@/lib/utils';

const SECTIONS: [string, string][] = [
  ['/home', 'Chat'],
  ['/inbox', 'Inbox'],
  ['/agents', 'Agents'],
  ['/teams', 'Teams'],
  ['/tasks', 'Tasks'],
  ['/notifications', 'Notifications'],
  ['/settings', 'Settings'],
  ['/api-keys', 'API keys'],
];

function sectionFor(pathname: string) {
  return SECTIONS.find(([path]) => pathname === path || pathname.startsWith(`${path}/`))?.[1];
}

export function BellIcon({ className = 'h-[17px] w-[17px]' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.9 1.9 0 0 0 3.4 0" />
    </svg>
  );
}

/**
 * Top bar: brand and breadcrumb, the command palette search, the notification
 * bell and, in the desktop app, the window controls. The desktop window has no
 * system frame, so this bar is also its drag handle.
 */
export default function TitleBar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const desktopApp = isDesktop();
  const { pathname } = useLocation();
  const notifications = useOptionalNotifications();
  const [bellOpen, setBellOpen] = useState(false);
  const section = sectionFor(pathname);

  useEffect(() => setBellOpen(false), [pathname]);

  return (
    <header
      data-tauri-drag-region
      className={cn(
        'relative z-30 h-[42px] flex-none select-none items-center gap-3 border-b border-line bg-panel pl-3.5',
        desktopApp ? 'flex' : 'hidden md:flex',
      )}
    >
      <div data-tauri-drag-region className="flex min-w-0 flex-1 items-center gap-2.5 md:w-[300px] md:flex-none">
        <span className="grid h-[22px] w-[22px] flex-none place-items-center rounded-[6px] bg-accent text-accent-foreground">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
        </span>
        <span data-tauri-drag-region className="font-mono text-[12px] font-medium tracking-[0.12em]">COGNITRIX</span>
        {section && (
          <span data-tauri-drag-region className="truncate font-mono text-[11px] text-fg-dim">/ {section}</span>
        )}
      </div>

      <div data-tauri-drag-region className="hidden flex-1 justify-center md:flex">
        <button
          type="button"
          onClick={onOpenPalette}
          className="flex h-7 w-full max-w-[440px] items-center gap-2 rounded-md border border-line bg-panel-2 px-2.5 text-[13px] text-fg-dim transition-colors hover:text-fg"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <span className="flex-1 text-left">Search sessions, agents, or run a command</span>
          <kbd className="rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px]">Ctrl K</kbd>
        </button>
      </div>

      <div className="flex items-center justify-end md:w-[300px]">
        {notifications && (
          <div className="relative">
            <button
              type="button"
              aria-label={notifications.unread ? `Notifications, ${notifications.unread} new` : 'Notifications'}
              aria-expanded={bellOpen}
              aria-haspopup="dialog"
              onClick={() => setBellOpen((open) => !open)}
              className={cn('relative grid h-[42px] w-10 place-items-center text-fg-dim transition-colors hover:text-fg', bellOpen && 'text-fg')}
            >
              <BellIcon />
              {notifications.unread > 0 && (
                <span className="absolute right-1 top-[7px] grid h-3.5 min-w-3.5 place-items-center rounded-full bg-[#ffb86b] px-0.5 font-mono text-[9px] font-medium text-[#0b0c0e]">
                  {notifications.unread > 9 ? '9+' : notifications.unread}
                </span>
              )}
            </button>
            {bellOpen && <NotificationPopover onClose={() => setBellOpen(false)} />}
          </div>
        )}
        {!desktopApp && (
          <Link to="/settings" aria-label="Settings" className="grid h-[42px] w-10 place-items-center text-fg-dim hover:text-fg">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" /></svg>
          </Link>
        )}
        {desktopApp && (
          <>
            <button type="button" aria-label="Minimize" onClick={() => windowAction('minimize')} className="grid h-[42px] w-[46px] place-items-center text-fg-dim hover:bg-panel-2 hover:text-fg">
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M0 5h10" stroke="currentColor" /></svg>
            </button>
            <button type="button" aria-label="Maximize" onClick={() => windowAction('toggleMaximize')} className="grid h-[42px] w-[46px] place-items-center text-fg-dim hover:bg-panel-2 hover:text-fg">
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y=".5" width="9" height="9" fill="none" stroke="currentColor" /></svg>
            </button>
            <button type="button" aria-label="Close" onClick={() => windowAction('close')} className="grid h-[42px] w-[46px] place-items-center text-fg-dim hover:bg-[#c42b1c] hover:text-white">
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M0 0l10 10M10 0 0 10" stroke="currentColor" /></svg>
            </button>
          </>
        )}
      </div>
    </header>
  );
}
