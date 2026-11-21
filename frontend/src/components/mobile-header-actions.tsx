import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { MobileSheet } from '@/components/MobileSheet';
import { Button } from '@/lib/components/ui/button';
import { tr } from '@/lib/i18n';

type MobileHeaderActionBase = {
  key: string;
  label: string;
  disabled?: boolean;
  destructive?: boolean;
};

export type MobileHeaderAction = MobileHeaderActionBase & (
  | { to: string; onSelect?: never }
  | { to?: never; onSelect: () => void }
);

export function MobileHeaderActions({
  id,
  label = 'Page actions',
  actions,
}: {
  id: string;
  label?: string;
  actions: readonly MobileHeaderAction[];
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const actionClassName = 'min-h-11 w-full justify-start px-3';

  return (
    <>
      <Button
        ref={triggerRef}
        type="button"
        variant="outline"
        size="icon"
        className="md:hidden"
        aria-label={tr('ui.mobileHeaderActions.moreActions')}
        aria-controls={id}
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="5" cy="12" r="1.5" />
          <circle cx="12" cy="12" r="1.5" />
          <circle cx="19" cy="12" r="1.5" />
        </svg>
      </Button>

      <MobileSheet id={id} label={label} open={open} onClose={() => setOpen(false)} triggerRef={triggerRef}>
        <div className="flex min-h-14 items-center justify-between border-b border-line px-4">
          <h2 className="font-mono text-[11px] tracking-[0.14em] text-fg-dim">{label}</h2>
          <Button type="button" variant="ghost" size="icon" aria-label={tr('ui.mobileHeaderActions.closePageActions')} onClick={() => setOpen(false)}>
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </Button>
        </div>
        <div className="grid gap-1 p-3">
          {actions.map((action) => {
            const className = `${actionClassName}${action.destructive ? ' text-danger-ink hover:text-danger-ink' : ''}`;

            if ('to' in action && action.to) {
              return (
                <Button key={action.key} asChild variant="ghost" size="md" className={className} onClick={() => setOpen(false)}>
                  <Link to={action.to}>{action.label}</Link>
                </Button>
              );
            }

            return (
              <Button
                key={action.key}
                type="button"
                variant="ghost"
                size="md"
                className={className}
                disabled={action.disabled}
                onClick={() => {
                  if (action.onSelect) action.onSelect();
                  setOpen(false);
                }}
              >
                {action.label}
              </Button>
            );
          })}
        </div>
      </MobileSheet>
    </>
  );
}


/** The same actions as a small "..." menu on wide screens, so rarely used and
 *  destructive ones don't sit beside the main button. */
export function DesktopMoreMenu({ actions }: { actions: readonly MobileHeaderAction[] }) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!boxRef.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', key);
    };
  }, [open]);

  if (actions.length === 0) return null;
  const itemClass = 'flex w-full items-center rounded px-3 py-2 text-left text-[13.5px] transition-colors hover:bg-panel-2 disabled:opacity-50';
  return (
    <div ref={boxRef} className="relative hidden md:block">
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="h-8 w-8"
        aria-label={tr('ui.mobileHeaderActions.moreActions')}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" />
        </svg>
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 top-full z-30 mt-1.5 min-w-48 rounded-md border border-line bg-panel p-1 shadow-lg">
          {actions.map((action) => action.to ? (
            <Link key={action.key} role="menuitem" to={action.to} onClick={() => setOpen(false)} className={itemClass}>{action.label}</Link>
          ) : (
            <button
              key={action.key}
              type="button"
              role="menuitem"
              disabled={action.disabled}
              onClick={() => { setOpen(false); action.onSelect?.(); }}
              className={itemClass + (action.destructive ? ' text-danger-ink' : '')}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
