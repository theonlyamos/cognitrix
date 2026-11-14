import { useEffect, useRef, useState } from 'react';

export interface ChatRowAction {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}

/** The "more" button on a chat list row: rename, pin, archive, export, delete. */
export function ChatRowMenu({ label, actions, compact = false }: { label: string; actions: ChatRowAction[]; /** A borderless button for the sidebar's rows. */ compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={root} className={compact ? 'relative mr-1 flex-none' : 'relative mr-2 flex-none'}>
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={compact
          ? 'grid h-11 w-9 place-items-center rounded-md text-fg-dim hover:bg-panel hover:text-fg md:h-7 md:w-7'
          : 'grid h-11 w-11 place-items-center rounded border border-line text-fg-dim hover:border-fg-dim hover:text-fg md:h-8 md:w-8'}
      >
        <span aria-hidden>⋯</span>
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-full z-30 mt-1 w-40 rounded border border-line bg-panel py-1 shadow-lg">
          {actions.map((action) => (
            <button
              key={action.label}
              type="button"
              role="menuitem"
              disabled={action.disabled}
              onClick={() => {
                setOpen(false);
                action.onSelect();
              }}
              className={`block min-h-11 w-full px-3 text-left text-[13px] transition-colors hover:bg-panel-2 disabled:cursor-not-allowed disabled:opacity-50 md:min-h-8 ${action.danger ? 'text-danger-ink' : 'text-fg'}`}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
