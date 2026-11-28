import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Esc closes, Tab stays inside, and focus goes back where it was after. */
function useTrap(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const before = document.activeElement as HTMLElement | null;
    const box = ref.current;
    const first = box?.querySelector<HTMLElement>('[autofocus]') ?? box?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? box)?.focus();
    return () => { if (before && document.contains(before)) before.focus(); };
  }, [open]);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const items = Array.from(ref.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    if (items.length === 0) return;
    const [first, last] = [items[0], items[items.length - 1]];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  return { ref, onKeyDown };
}

/** A centred dialog over the page (a full screen sheet on phones). */
export function Modal({ open, title, onClose, children, footer, className }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; className?: string;
}) {
  const { ref, onKeyDown } = useTrap(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 md:items-start md:p-6 md:pt-[8vh]" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} onKeyDown={onKeyDown}
        className={cn('flex max-h-[92vh] w-full flex-col rounded-t-2xl border border-line bg-panel shadow-2xl outline-none md:max-w-[560px] md:rounded-2xl', className)}>
        <div className="flex flex-none items-center gap-2 px-5 pb-2 pt-4">
          <h2 className="flex-1 text-[16px] font-semibold tracking-tight">{title}</h2>
          <button type="button" onClick={onClose} aria-label={tr('ui.meetings.overlay.close')} className="flex h-9 w-9 items-center justify-center rounded-lg text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8">
            <X size={16} aria-hidden />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4">{children}</div>
        {footer && <div className="flex flex-none flex-wrap items-center gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

/** A panel that slides in from the right (full screen on phones). */
export function Sheet({ open, title, onClose, children, footer }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode; footer?: ReactNode;
}) {
  const { ref, onKeyDown } = useTrap(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} onKeyDown={onKeyDown}
        className="flex h-full w-full flex-col border-l border-line bg-panel shadow-2xl outline-none md:max-w-[480px]">
        <div className="flex flex-none items-center gap-2 px-5 pb-1 pt-4">
          <h2 className="flex-1 text-[16px] font-semibold tracking-tight">{title}</h2>
          <button type="button" onClick={onClose} aria-label={tr('ui.meetings.overlay.close')} className="flex h-9 w-9 items-center justify-center rounded-lg text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8">
            <X size={16} aria-hidden />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4">{children}</div>
        {footer && <div className="flex flex-none items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

/** A small menu under a button: items close it when picked, so does a click outside or Esc. */
export function Menu({ trigger, label, children, align = 'right', className }: {
  trigger: (props: { open: boolean; toggle: () => void }) => ReactNode;
  label: string;
  children: (close: () => void) => ReactNode;
  align?: 'left' | 'right';
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false); };
    const esc = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  }, [open]);
  return (
    <div ref={box} className={cn('relative inline-flex', className)}>
      {trigger({ open, toggle: () => setOpen((v) => !v) })}
      {open && (
        <div role="menu" aria-label={label}
          className={cn('absolute top-full z-40 mt-1 min-w-[200px] rounded-xl border border-line bg-panel p-1 shadow-xl', align === 'right' ? 'right-0' : 'left-0')}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

export function MenuItem({ icon, title, hint, onSelect, disabled, danger }: {
  icon?: ReactNode; title: string; hint?: string; onSelect: () => void; disabled?: boolean; danger?: boolean;
}) {
  return (
    <button type="button" role="menuitem" disabled={disabled} onClick={onSelect}
      className={cn('flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] hover:bg-panel-2 disabled:pointer-events-none disabled:opacity-50', danger && 'text-danger-ink')}>
      {icon && <span className={cn('mt-0.5 flex-none', danger ? 'text-danger-ink' : 'text-fg-dim')}>{icon}</span>}
      <span className="min-w-0">
        <span className="block font-medium">{title}</span>
        {hint && <span className="block text-[12px] text-fg-dim">{hint}</span>}
      </span>
    </button>
  );
}
