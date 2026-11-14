import { useEffect, useState } from 'react';
import {
  LayoutDashboard as DashboardIcon, Inbox as InboxIcon, Bot as BotIcon, ListChecks as ListChecksIcon, Zap as ZapIcon,
  Users as UsersIcon, BookOpen as BookOpenIcon, LayoutPanelTop as LayoutPanelTopIcon, Mic as MicIcon,
  Plus as PlusIcon, Search as SearchIcon, Ellipsis as MoreIcon, PanelLeft as PanelLeftIcon, type LucideIcon,
} from 'lucide-react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useOptionalNotifications } from '@/context/NotificationsContext';
import AccountMenu from '@/components/AccountMenu';
import NotificationPopover from '@/components/NotificationPopover';
import SidebarChats from '@/components/SidebarChats';
import { BellIcon } from '@/components/TitleBar';
import { newChatHref } from '@/lib/chatNav';
import { useT, type MessageKey } from '@/lib/i18n';
import { cn } from '@/lib/utils';

type NavItem = { path: string; label: MessageKey; icon: LucideIcon };

// The everyday places. Chats are the list below, so there is no Chat item.
const NAV: NavItem[] = [
  { path: '/dashboard', label: 'nav.dashboard', icon: DashboardIcon },
  { path: '/inbox', label: 'nav.inbox', icon: InboxIcon },
  { path: '/agents', label: 'nav.agents', icon: BotIcon },
  { path: '/artifacts', label: 'nav.artifacts', icon: LayoutPanelTopIcon },
  { path: '/automations', label: 'nav.automations', icon: ZapIcon },
  { path: '/knowledge', label: 'nav.knowledge', icon: BookOpenIcon },
];
// Behind "More".
const MORE: NavItem[] = [
  { path: '/tasks', label: 'nav.tasks', icon: ListChecksIcon },
  { path: '/teams', label: 'nav.teams', icon: UsersIcon },
  { path: '/meetings', label: 'nav.meetings', icon: MicIcon },
];

const COLLAPSED_KEY = 'sidebar.collapsed';

function loadCollapsed() {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

interface SidebarProps {
  mobileOpen?: boolean;
  onNavigate?: () => void;
  /** Opens the Ctrl K search. */
  onOpenSearch?: () => void;
}

/**
 * The one sidebar: New chat, search, the main places, every chat, and the
 * account. Collapsed (wide screens), it is a rail of icons and agent
 * thumbnails; on a phone it is a drawer.
 */
export default function Sidebar({ mobileOpen = false, onNavigate, onOpenSearch }: SidebarProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const t = useT();
  const notifications = useOptionalNotifications();
  const waiting = notifications?.inbox.length ?? 0;
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const isActive = (path: string) => location.pathname === path || location.pathname.startsWith(`${path}/`);
  const moreActive = MORE.some((item) => isActive(item.path));
  const [moreOpen, setMoreOpen] = useState(moreActive);
  const [bellOpen, setBellOpen] = useState(false);
  useEffect(() => { if (moreActive) setMoreOpen(true); }, [moreActive]);
  useEffect(() => setBellOpen(false), [location.pathname]);

  const toggle = () =>
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0');
      } catch {
        /* ignore */
      }
      return next;
    });
  // Only wide screens collapse; the phone drawer is always full.
  const slim = (cls: string) => (collapsed ? cls : '');

  const link = (item: NavItem) => {
    const active = isActive(item.path);
    const Icon = item.icon;
    const label = t(item.label);
    return (
      <Link
        key={item.path}
        to={item.path}
        aria-current={active ? 'page' : undefined}
        onClick={onNavigate}
        title={collapsed ? label : undefined}
        className={cn(
          'relative flex min-h-11 items-center gap-3 rounded-lg px-2.5 text-[14px] transition-colors md:min-h-[34px]',
          slim('md:justify-center md:px-0'),
          active ? 'bg-panel-2 font-medium text-fg' : 'text-fg/85 hover:bg-panel-2 hover:text-fg',
        )}
      >
        <span className="relative flex-none">
          <Icon className="h-[17px] w-[17px]" strokeWidth={1.9} aria-hidden="true" />
          {item.path === '/inbox' && waiting > 0 && collapsed && (
            <span aria-hidden="true" className="absolute -right-1.5 -top-1.5 hidden h-2.5 w-2.5 rounded-full border-2 border-panel bg-accent md:block" />
          )}
        </span>
        <span className={cn('flex-1 truncate', slim('md:sr-only'))}>{label}</span>
        {item.path === '/inbox' && waiting > 0 && (
          <span className={cn('rounded-full bg-accent px-1.5 text-[11px] font-semibold leading-[18px] text-accent-foreground', slim('md:hidden'))}>
            {waiting}
            <span className="sr-only"> {t('nav.waiting')}</span>
          </span>
        )}
      </Link>
    );
  };

  return (
    <aside
      id="primary-navigation"
      aria-label="Primary navigation"
      data-mobile-open={mobileOpen}
      data-collapsed={collapsed}
      className={cn(
        'fixed inset-y-0 left-0 z-50 flex h-dvh w-[min(88vw,300px)] flex-none flex-col border-r border-line bg-panel transition-transform duration-200 md:visible md:static md:z-auto md:h-full md:translate-x-0 md:transition-[width]',
        mobileOpen ? 'visible translate-x-0' : 'invisible -translate-x-full',
        collapsed ? 'md:w-16' : 'md:w-[264px]',
      )}
    >
      {/* Brand and collapse */}
      <div className={cn('flex flex-none items-center gap-2.5 px-3.5 pb-2 pt-3.5', slim('md:flex-col md:gap-2 md:px-0'))}>
        <Link to="/dashboard" onClick={onNavigate} aria-label="Cognitrix home" className="flex min-h-11 min-w-0 flex-1 items-center gap-2.5 md:min-h-0">
          <span className="grid h-7 w-7 flex-none place-items-center rounded-lg bg-accent text-accent-foreground">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
          </span>
          <span className={cn('truncate text-[16px] font-semibold tracking-[-0.01em]', slim('md:hidden'))}>Cognitrix</span>
        </Link>
        <button
          type="button"
          onClick={toggle}
          aria-label={collapsed ? t('nav.expandSidebar') : t('nav.collapseSidebar')}
          aria-expanded={!collapsed}
          title={collapsed ? t('nav.expandSidebar') : t('nav.collapseSidebar')}
          className="hidden h-8 w-8 flex-none place-items-center rounded-lg text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:grid"
        >
          <PanelLeftIcon className="h-[17px] w-[17px]" strokeWidth={1.9} aria-hidden="true" />
        </button>
      </div>

      {/* New chat and search */}
      <div className={cn('flex flex-none flex-col gap-1 px-2.5', slim('md:items-center md:px-0'))}>
        <button
          type="button"
          onClick={() => { onNavigate?.(); navigate(newChatHref()); }}
          title={collapsed ? t('nav.newChat') : undefined}
          className={cn(
            'flex min-h-11 items-center gap-2.5 rounded-xl border border-line bg-bg px-3 text-[14px] font-medium transition-colors hover:border-fg-dim md:min-h-10',
            slim('md:h-10 md:w-10 md:justify-center md:px-0'),
          )}
        >
          <PlusIcon className="h-4 w-4 flex-none" strokeWidth={2.2} aria-hidden="true" />
          <span className={cn('flex-1 text-left', slim('md:sr-only'))}>{t('nav.newChat')}</span>
        </button>
        <button
          type="button"
          onClick={() => { onNavigate?.(); onOpenSearch?.(); }}
          title={collapsed ? `${t('nav.search')} (Ctrl K)` : undefined}
          className={cn(
            'flex min-h-11 items-center gap-2.5 rounded-lg px-3 text-[14px] text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:min-h-9',
            slim('md:h-10 md:w-10 md:justify-center md:px-0'),
          )}
        >
          <SearchIcon className="h-4 w-4 flex-none" strokeWidth={1.9} aria-hidden="true" />
          <span className={cn('flex-1 text-left', slim('md:sr-only'))}>{t('nav.search')}</span>
          <kbd className={cn('hidden rounded border border-line px-1.5 font-mono text-[10.5px] md:inline', slim('md:!hidden'))}>Ctrl K</kbd>
        </button>
      </div>

      {/* The main places */}
      <nav aria-label="Main" className={cn('flex flex-none flex-col gap-px px-2.5 pt-2', slim('md:px-3'))}>
        {NAV.map(link)}
        <button
          type="button"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen((v) => !v)}
          title={collapsed ? t('nav.more') : undefined}
          className={cn(
            'flex min-h-11 items-center gap-3 rounded-lg px-2.5 text-[14px] text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:min-h-[34px]',
            slim('md:justify-center md:px-0'),
          )}
        >
          <MoreIcon className="h-[17px] w-[17px] flex-none" strokeWidth={1.9} aria-hidden="true" />
          <span className={cn('flex-1 text-left', slim('md:sr-only'))}>{t('nav.more')}</span>
          <svg aria-hidden width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" className={cn('flex-none transition-transform', !moreOpen && '-rotate-90', slim('md:hidden'))}><path d="m6 9 6 6 6-6" /></svg>
        </button>
        {moreOpen && (
          <div className={cn('flex flex-col gap-px pl-3', slim('md:pl-0'))}>
            {MORE.map(link)}
          </div>
        )}
      </nav>

      {collapsed && <span aria-hidden className="mx-auto my-2 hidden h-px w-7 flex-none bg-line md:block" />}

      {/* Every chat (the phone drawer always shows the full list) */}
      <div className={cn('flex min-h-0 flex-1 flex-col px-2', slim('md:px-0'))}>
        <SidebarChats collapsed={collapsed} onExpand={toggle} onNavigate={onNavigate} />
      </div>

      {/* Account and notifications */}
      <div className={cn('flex flex-none items-center gap-1 border-t border-line p-2', slim('md:flex-col-reverse md:px-0'))}>
        <div className={cn('min-w-0 flex-1', slim('md:flex-none'))}>
          <AccountMenu collapsed={collapsed} onNavigate={onNavigate} />
        </div>
        {notifications && (
          <div className="relative flex-none">
            <button
              type="button"
              aria-label={notifications.unread ? `${t('nav.notifications')}, ${notifications.unread} new` : t('nav.notifications')}
              aria-expanded={bellOpen}
              aria-haspopup="dialog"
              onClick={() => setBellOpen((open) => !open)}
              className={cn('relative grid h-11 w-11 place-items-center rounded-lg text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:h-9 md:w-9', bellOpen && 'bg-panel-2 text-fg')}
            >
              <BellIcon />
              {notifications.unread > 0 && (
                <span className="absolute right-1 top-1 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-accent px-0.5 text-[9px] font-semibold text-accent-foreground">
                  {notifications.unread > 9 ? '9+' : notifications.unread}
                </span>
              )}
            </button>
            {bellOpen && <NotificationPopover placement="sidebar" onClose={() => setBellOpen(false)} />}
          </div>
        )}
      </div>
    </aside>
  );
}
