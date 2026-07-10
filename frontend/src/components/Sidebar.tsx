import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useUser } from '@/context/AppContext';
import { ThemeToggle } from '@/components/ThemeToggle';
import { cn } from '@/lib/utils';

type IconProps = { className?: string };

const NAV = [
  {
    path: '/home',
    label: 'Chat',
    icon: (p: IconProps) => (
      <svg {...p} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12l9-8 9 8" /><path d="M5 10v10h14V10" /></svg>
    ),
  },
  {
    path: '/agents',
    label: 'Agents',
    icon: (p: IconProps) => (
      <svg {...p} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="13" rx="1.5" /><path d="M8 20h8M12 17v3" /></svg>
    ),
  },
  {
    path: '/tasks',
    label: 'Tasks',
    icon: (p: IconProps) => (
      <svg {...p} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M9 5h11M9 12h11M9 19h11" /><path d="M4 5l1.4 1.4L8 4M4 12l1.4 1.4L8 11M4 19l1.4 1.4L8 18" /></svg>
    ),
  },
  {
    path: '/teams',
    label: 'Teams',
    icon: (p: IconProps) => (
      <svg {...p} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="9" cy="8" r="3" /><path d="M3 20c0-3 2.5-5 6-5s6 2 6 5" /><path d="M16 6a3 3 0 0 1 0 6M21 20c0-2.5-1.5-4-3.5-4.5" /></svg>
    ),
  },
  {
    path: '/api-keys',
    label: 'API Keys',
    icon: (p: IconProps) => (
      <svg {...p} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="8" cy="15" r="4" /><path d="M10.85 12.15 19 4M18 5l2 2M15 8l2 2" /></svg>
    ),
  },
];

interface SidebarProps {
  mobileOpen?: boolean;
  onNavigate?: () => void;
}

export default function Sidebar({ mobileOpen = false, onNavigate }: SidebarProps) {
  const location = useLocation();
  const { user, logout } = useUser();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sidebarCollapsed') === '1');

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
        'fixed inset-y-0 left-0 z-50 flex h-dvh w-[min(88vw,280px)] flex-none flex-col border-r border-line bg-panel transition-transform duration-200 md:visible md:static md:z-auto md:h-screen md:translate-x-0 md:transition-[width]',
        mobileOpen ? 'visible translate-x-0' : 'invisible -translate-x-full',
        collapsed ? 'md:w-[58px]' : 'md:w-[232px]',
      )}
    >
      {/* Brand */}
      <div
        className={cn(
          'flex items-center gap-2.5 border-b border-line px-4 py-4',
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
        <div className={cn('px-2 pb-1.5 pt-2 font-mono text-[10px] tracking-[0.16em] text-fg-dim', collapsed && 'md:hidden')}>WORKSPACE</div>
        {NAV.map((item) => {
          const active = isActive(item.path);
          return (
            <Link
              key={item.path}
              to={item.path}
              aria-current={active ? 'page' : undefined}
              onClick={onNavigate}
              title={collapsed ? item.label : undefined}
              className={cn(
                'group relative flex min-h-11 items-center gap-3 rounded px-2.5 py-2 text-[14px] font-medium transition-colors md:min-h-0',
                collapsed && 'md:justify-center md:gap-0 md:px-0',
                active ? 'bg-panel-2 text-fg' : 'text-fg-dim hover:bg-panel-2 hover:text-fg',
              )}
            >
              {active && <span className={cn('absolute -left-2.5 bottom-1.5 top-1.5 w-[3px] rounded-r bg-accent', collapsed && 'md:left-0')} />}
              <item.icon className="h-[17px] w-[17px] flex-none" />
              <span className={cn(collapsed && 'md:hidden')}>{item.label}</span>
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
            title={collapsed ? 'Sign out' : undefined}
            className={cn(
              'flex h-11 flex-1 items-center justify-center gap-2 rounded border border-line font-mono text-[11px] tracking-[0.04em] text-fg-dim transition-colors hover:border-danger hover:text-danger-ink md:h-9',
              collapsed && 'md:w-9 md:flex-none',
            )}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></svg>
            <span className={cn(collapsed && 'md:hidden')}>SIGN OUT</span>
          </button>
        </div>

        <button
          type="button"
          onClick={toggle}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={cn(
            'mt-1.5 hidden h-8 w-full items-center justify-center gap-2 rounded text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:flex',
            !collapsed && 'font-mono text-[10.5px] tracking-[0.08em]',
          )}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={cn('transition-transform', collapsed && 'rotate-180')}>
            <path d="M15 6l-6 6 6 6" />
          </svg>
          {!collapsed && 'COLLAPSE'}
        </button>
      </div>
    </aside>
  );
}
