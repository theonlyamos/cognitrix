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

export default function Sidebar() {
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
      className={cn(
        'flex h-screen flex-none flex-col border-r border-line bg-panel transition-[width] duration-200',
        collapsed ? 'w-[58px]' : 'w-[232px]',
      )}
    >
      {/* Brand */}
      <div className={cn('flex items-center border-b border-line py-4', collapsed ? 'justify-center px-0' : 'gap-2.5 px-4')}>
        <span className="grid h-6 w-6 flex-none place-items-center rounded-sm bg-accent text-accent-foreground">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
        </span>
        {!collapsed && (
          <span className="font-mono text-[12px] font-bold tracking-[0.13em]">
            COGNITRIX<span className="font-medium text-fg-dim"> /v0.2.6</span>
          </span>
        )}
      </div>

      {/* Nav */}
      <nav className={cn('flex flex-col gap-0.5 py-3', collapsed ? 'px-2' : 'px-2.5')}>
        {!collapsed && <div className="px-2 pb-1.5 pt-2 font-mono text-[10px] tracking-[0.16em] text-fg-dim">WORKSPACE</div>}
        {NAV.map((item) => {
          const active = isActive(item.path);
          return (
            <Link
              key={item.path}
              to={item.path}
              title={collapsed ? item.label : undefined}
              className={cn(
                'group relative flex items-center rounded py-2 text-[14px] font-medium transition-colors',
                collapsed ? 'justify-center px-0' : 'gap-3 px-2.5',
                active ? 'bg-panel-2 text-fg' : 'text-fg-dim hover:bg-panel-2 hover:text-fg',
              )}
            >
              {active && <span className={cn('absolute top-1.5 bottom-1.5 w-[3px] rounded-r bg-accent', collapsed ? 'left-0' : '-left-2.5')} />}
              <item.icon className="h-[17px] w-[17px] flex-none" />
              {!collapsed && item.label}
            </Link>
          );
        })}
      </nav>

      {/* Command palette hint (wired in Phase 3) */}
      <button
        type="button"
        title={collapsed ? 'Search & run (⌘K)' : undefined}
        className={cn(
          'mx-2.5 flex items-center rounded border border-line py-2 text-[12.5px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg',
          collapsed ? 'justify-center px-0' : 'gap-2 px-2.5',
        )}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" /></svg>
        {!collapsed && (
          <>
            Search &amp; run
            <kbd className="ml-auto rounded border border-line px-1.5 py-px font-mono text-[10px] text-fg-dim">⌘K</kbd>
          </>
        )}
      </button>

      {/* Footer */}
      <div className="mt-auto border-t border-line p-2.5">
        {!collapsed && (
          <div className="mb-1.5 flex items-center gap-2.5 rounded px-2 py-1.5">
            <span className="grid h-7 w-7 flex-none place-items-center rounded-sm border border-line bg-panel-2 text-[12px] font-bold text-accent-ink">
              {(user?.name?.[0] || 'U').toUpperCase()}
            </span>
            <div className="min-w-0 leading-tight">
              <div className="truncate text-[13px] font-semibold">{user?.name || 'User'}</div>
              <div className="truncate font-mono text-[10.5px] text-fg-dim">{user?.email}</div>
            </div>
          </div>
        )}

        <div className={cn('flex items-center gap-1.5', collapsed && 'flex-col')}>
          <ThemeToggle className="flex-none" />
          <button
            type="button"
            onClick={logout}
            title={collapsed ? 'Sign out' : undefined}
            className={cn(
              'flex h-9 items-center justify-center gap-2 rounded border border-line font-mono text-[11px] tracking-[0.04em] text-fg-dim transition-colors hover:border-danger hover:text-danger-ink',
              collapsed ? 'w-9 flex-none' : 'flex-1',
            )}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></svg>
            {!collapsed && 'SIGN OUT'}
          </button>
        </div>

        <button
          type="button"
          onClick={toggle}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={cn(
            'mt-1.5 flex h-8 w-full items-center justify-center gap-2 rounded text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg',
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
