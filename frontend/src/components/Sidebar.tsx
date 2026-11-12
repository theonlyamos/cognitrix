import { useState } from 'react';
import { LayoutDashboard as DashboardIcon, Inbox as InboxIcon, MessageSquare as MessageSquareIcon, Bot as BotIcon, ListChecks as ListChecksIcon, Zap as ZapIcon, Users as UsersIcon, BookOpen as BookOpenIcon, Mic as MicIcon, Settings as SettingsIcon, KeyRound as KeyRoundIcon, type LucideIcon } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { useUser } from '@/context/AppContext';
import { useOptionalNotifications } from '@/context/NotificationsContext';
import { isDesktop } from '@/lib/desktop';
import { ThemeToggle } from '@/components/ThemeToggle';
import { useT, type MessageKey } from '@/lib/i18n';
import { cn } from '@/lib/utils';

type IconProps = { className?: string };

// Each icon says what's behind it: a tray for the inbox, a speech bubble for
// chat, a robot for agents, a checklist for tasks, a lightning bolt for
// automations, people for teams, a gear for settings and a key for API keys.
const icon = (Icon: LucideIcon) => (p: IconProps) => <Icon {...p} strokeWidth={1.9} aria-hidden="true" />;

const NAV: { path: string; label: MessageKey; icon: (p: IconProps) => JSX.Element }[] = [
  { path: '/dashboard', label: 'nav.dashboard', icon: icon(DashboardIcon) },
  { path: '/inbox', label: 'nav.inbox', icon: icon(InboxIcon) },
  { path: '/home', label: 'nav.chat', icon: icon(MessageSquareIcon) },
  { path: '/agents', label: 'nav.agents', icon: icon(BotIcon) },
  { path: '/tasks', label: 'nav.tasks', icon: icon(ListChecksIcon) },
  { path: '/automations', label: 'nav.automations', icon: icon(ZapIcon) },
  { path: '/teams', label: 'nav.teams', icon: icon(UsersIcon) },
  { path: '/knowledge', label: 'nav.knowledge', icon: icon(BookOpenIcon) },
  { path: '/meetings', label: 'nav.meetings', icon: icon(MicIcon) },
  { path: '/settings', label: 'nav.settings', icon: icon(SettingsIcon) },
  { path: '/api-keys', label: 'nav.apiKeys', icon: icon(KeyRoundIcon) },
];

interface SidebarProps {
  mobileOpen?: boolean;
  onNavigate?: () => void;
}

export default function Sidebar({ mobileOpen = false, onNavigate }: SidebarProps) {
  const location = useLocation();
  const t = useT();
  const { user, logout } = useUser();
  const waiting = useOptionalNotifications()?.inbox.length ?? 0;
  // The desktop app opens with the slim icon rail from the designs; the web
  // keeps the full sidebar until someone collapses it.
  const [collapsed, setCollapsed] = useState(() => {
    const stored = localStorage.getItem('sidebarCollapsed');
    return stored === null ? isDesktop() : stored === '1';
  });

  const toggle = () =>
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem('sidebarCollapsed', next ? '1' : '0');
      return next;
    });

  const isActive = (path: string) =>
    path === '/home' ? location.pathname === '/home' : location.pathname.startsWith(path);

  return (
    <aside
      id="primary-navigation"
      aria-label="Primary navigation"
      data-mobile-open={mobileOpen}
      className={cn(
        'fixed inset-y-0 left-0 z-50 flex h-dvh w-[min(88vw,280px)] flex-none flex-col border-r border-line bg-panel transition-transform duration-200 md:visible md:static md:z-auto md:h-full md:translate-x-0 md:transition-[width]',
        mobileOpen ? 'visible translate-x-0' : 'invisible -translate-x-full',
        collapsed ? 'md:w-[58px]' : 'md:w-[232px]',
      )}
    >
      {/* Brand (the title bar shows it on wider screens) */}
      <div
        className={cn(
          'flex items-center gap-2.5 border-b border-line px-4 py-4 md:hidden',
          collapsed && 'md:justify-center md:gap-0 md:px-0',
        )}
      >
        <span className="grid h-6 w-6 flex-none place-items-center rounded-sm bg-accent text-accent-foreground">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
        </span>
        <span className={cn('font-mono text-[12px] font-bold tracking-[0.13em]', collapsed && 'md:hidden')}>
          COGNITRIX<span className="font-medium text-fg-dim"> /v{__APP_VERSION__}</span>
        </span>
      </div>

      {/* Nav */}
      <nav className={cn('flex flex-col gap-0.5 px-2.5 py-3', collapsed && 'md:px-2')}>
        <div className={cn('px-2 pb-1.5 pt-2 font-mono text-[10px] tracking-[0.16em] text-fg-dim', collapsed && 'md:hidden')}>{t('nav.workspace')}</div>
        {NAV.map((item) => {
          const active = isActive(item.path);
          return (
            <Link
              key={item.path}
              to={item.path}
              aria-current={active ? 'page' : undefined}
              onClick={onNavigate}
              title={collapsed ? t(item.label) : undefined}
              className={cn(
                'group relative flex min-h-11 items-center gap-3 rounded px-2.5 py-2 text-[14px] font-medium transition-colors md:min-h-0',
                collapsed && 'md:justify-center md:gap-0 md:px-0',
                active ? 'bg-panel-2 text-fg' : 'text-fg-dim hover:bg-panel-2 hover:text-fg',
              )}
            >
              {active && <span className={cn('absolute -left-2.5 bottom-1.5 top-1.5 w-[3px] rounded-r bg-accent', collapsed && 'md:left-0')} />}
              <span className="relative flex-none">
                <item.icon className="h-[17px] w-[17px] flex-none" />
                {item.path === '/inbox' && waiting > 0 && collapsed && (
                  <span aria-hidden="true" className="absolute -right-2 -top-2 hidden h-[15px] min-w-[15px] place-items-center rounded-full bg-[#ffb86b] px-0.5 font-mono text-[9.5px] font-medium text-[#0b0c0e] md:grid">
                    {waiting}
                  </span>
                )}
              </span>
              <span className={cn('flex-1', collapsed && 'md:hidden')}>{t(item.label)}</span>
              {item.path === '/inbox' && waiting > 0 && (
                <span className={cn('rounded-full bg-[#ffb86b]/15 px-1.5 font-mono text-[10.5px] text-[#c77a26] dark:text-[#ffb86b]', collapsed && 'md:hidden')}>
                  {waiting}
                  <span className="sr-only"> {t('nav.waiting')}</span>
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="mt-auto border-t border-line p-2.5">
        <div className={cn('mb-1.5 flex items-center gap-2.5 rounded px-2 py-1.5', collapsed && 'md:hidden')}>
          <span className="grid h-7 w-7 flex-none place-items-center rounded-sm border border-line bg-panel-2 text-[12px] font-bold text-accent-ink">
            {(user?.name?.[0] || 'U').toUpperCase()}
          </span>
          <div className="min-w-0 leading-tight">
            <div className="truncate text-[13px] font-semibold">{user?.name || 'User'}</div>
            <div className="truncate font-mono text-[10.5px] text-fg-dim">{user?.email}</div>
          </div>
        </div>

        <div className={cn('flex items-center gap-1.5', collapsed && 'md:flex-col')}>
          <ThemeToggle className="flex-none" />
          <button
            type="button"
            onClick={logout}
            title={collapsed ? t('nav.signOut') : undefined}
            className={cn(
              'flex h-11 flex-1 items-center justify-center gap-2 rounded border border-line font-mono text-[11px] tracking-[0.04em] text-fg-dim transition-colors hover:border-danger hover:text-danger-ink md:h-9',
              collapsed && 'md:w-9 md:flex-none',
            )}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></svg>
            <span className={cn('uppercase', collapsed && 'md:hidden')}>{t('nav.signOut')}</span>
          </button>
        </div>

        <button
          type="button"
          onClick={toggle}
          title={collapsed ? t('nav.expandSidebar') : t('nav.collapseSidebar')}
          className={cn(
            'mt-1.5 hidden h-8 w-full items-center justify-center gap-2 rounded text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:flex',
            !collapsed && 'font-mono text-[10.5px] tracking-[0.08em]',
          )}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={cn('transition-transform', collapsed && 'rotate-180')}>
            <path d="M15 6l-6 6 6 6" />
          </svg>
          {!collapsed && <span className="uppercase">{t('nav.collapse')}</span>}
        </button>
      </div>
    </aside>
  );
}
