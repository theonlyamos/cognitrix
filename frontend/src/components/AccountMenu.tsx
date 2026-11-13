import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { KeyRound, LogOut, Monitor, Moon, Settings, Sun, type LucideIcon } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { useUser } from '@/context/AppContext';
import { useTheme, type ThemeMode } from '@/context/ThemeContext';
import { useT } from '@/lib/i18n';
import { cn } from '@/lib/utils';

const THEMES: { mode: ThemeMode; label: 'appearance.theme.light' | 'appearance.theme.dark' | 'appearance.theme.system'; Icon: LucideIcon }[] = [
  { mode: 'light', label: 'appearance.theme.light', Icon: Sun },
  { mode: 'dark', label: 'appearance.theme.dark', Icon: Moon },
  { mode: 'system', label: 'appearance.theme.system', Icon: Monitor },
];

/**
 * The sidebar's account button: your name and email, opening a menu with
 * Settings, API Keys, the theme and Sign out (same look as the composer's + menu).
 */
export default function AccountMenu({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const t = useT();
  const { user, logout } = useUser();
  const { mode, setMode } = useTheme();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  // The sidebar clips its overflow, so the menu is drawn in a portal and
  // placed above the button (wider than the slim rail when collapsed).
  const [place, setPlace] = useState<CSSProperties>({});
  useLayoutEffect(() => {
    if (!open) return;
    const at = () => {
      const r = buttonRef.current?.getBoundingClientRect();
      if (!r) return;
      setPlace({ left: r.left, bottom: window.innerHeight - r.top + 8, width: Math.max(r.width, 240) });
    };
    at();
    window.addEventListener('resize', at);
    return () => window.removeEventListener('resize', at);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (rootRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Close when the page changes (a menu link was followed).
  useEffect(() => setOpen(false), [location.pathname]);

  const here = location.pathname.startsWith('/settings') || location.pathname.startsWith('/api-keys');
  const initial = (user?.name?.[0] || 'U').toUpperCase();
  const item = 'flex w-full min-h-11 items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[14px] transition-colors hover:bg-panel-2 md:min-h-0 md:text-[13px] [&>svg]:h-[15px] [&>svg]:w-[15px] [&>svg]:flex-none [&>svg]:text-fg-dim';
  const link = (to: string, label: string, Icon: LucideIcon) => {
    const active = location.pathname.startsWith(to);
    return (
      <Link
        to={to}
        role="menuitem"
        aria-current={active ? 'page' : undefined}
        onClick={() => { setOpen(false); onNavigate?.(); }}
        className={cn(item, active && 'bg-panel-2 text-fg')}
      >
        <Icon strokeWidth={1.9} aria-hidden="true" />
        <span className="flex-1">{label}</span>
      </Link>
    );
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={t('nav.account')}
        aria-haspopup="menu"
        aria-expanded={open}
        title={collapsed ? `${user?.name || 'User'} · ${t('nav.account')}` : undefined}
        className={cn(
          'flex min-h-11 w-full items-center gap-2.5 rounded px-2 py-1.5 text-left transition-colors hover:bg-panel-2 md:min-h-0',
          (open || here) && 'bg-panel-2',
          collapsed && 'md:justify-center md:px-0',
        )}
      >
        <span className="grid h-7 w-7 flex-none place-items-center rounded-sm border border-line bg-panel-2 text-[12px] font-bold text-accent-ink">
          {initial}
        </span>
        <span className={cn('min-w-0 flex-1 leading-tight', collapsed && 'md:hidden')}>
          <span className="block truncate text-[13px] font-semibold">{user?.name || 'User'}</span>
          <span className="block truncate font-mono text-[10.5px] text-fg-dim">{user?.email}</span>
        </span>
        <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className={cn('flex-none text-fg-dim transition-transform', open && 'rotate-180', collapsed && 'md:hidden')}>
          <path d="M18 15l-6-6-6 6" />
        </svg>
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          role="menu"
          aria-label={t('nav.account')}
          style={place}
          className="animate-rise fixed z-[60] rounded-lg border border-line bg-panel p-1.5 text-fg shadow-lg"
        >
          <div className="px-2.5 pb-1.5 pt-1 leading-tight">
            <div className="truncate text-[13px] font-semibold">{user?.name || 'User'}</div>
            <div className="truncate font-mono text-[10.5px] text-fg-dim">{user?.email}</div>
          </div>
          <div className="mx-1 my-1 h-px bg-line" />
          {link('/settings', t('nav.settings'), Settings)}
          {link('/api-keys', t('nav.apiKeys'), KeyRound)}
          <div className="mx-1 my-1 h-px bg-line" />
          <div className="px-2.5 pb-1 pt-1.5 font-mono text-[10px] tracking-[0.14em] text-fg-dim">{t('appearance.theme').toUpperCase()}</div>
          <div role="radiogroup" aria-label={t('appearance.theme')} className="grid grid-cols-3 gap-1 px-1.5 pb-1.5">
            {THEMES.map(({ mode: m, label, Icon }) => {
              const on = mode === m;
              return (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  onClick={() => setMode(m)}
                  className={cn(
                    'flex min-h-10 flex-col items-center justify-center gap-1 rounded border text-[11.5px] transition-colors md:min-h-0 md:py-1.5',
                    on ? 'border-accent bg-accent text-accent-foreground' : 'border-line text-fg-dim hover:border-fg-dim hover:text-fg',
                  )}
                >
                  <Icon className="h-[14px] w-[14px]" strokeWidth={1.9} aria-hidden="true" />
                  {t(label)}
                </button>
              );
            })}
          </div>
          <div className="mx-1 my-1 h-px bg-line" />
          <button type="button" role="menuitem" onClick={() => { setOpen(false); logout(); }} className={cn(item, 'text-danger-ink [&>svg]:text-danger-ink')}>
            <LogOut strokeWidth={1.9} aria-hidden="true" />
            <span className="flex-1">{t('nav.signOut')}</span>
          </button>
        </div>,
        document.body,
      )}
    </div>
  );
}
