import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { REASONING_LEVELS } from '@/lib/reasoning';
import { cn } from '@/lib/utils';

const OPTIONS = [{ value: '', label: 'agent default' }, ...REASONING_LEVELS.map((l) => ({ value: l.value, label: l.label.toLowerCase() }))];

/** Themed reasoning-level picker for the chat composer. A native <select> pops
 *  up a white OS list in dark mode (notably WebView2), so this draws its own. */
export default function ReasoningPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();
  const selectedIndex = Math.max(0, OPTIONS.findIndex((o) => o.value === value));

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    listRef.current?.focus();
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const show = () => {
    setActive(selectedIndex);
    setOpen(true);
  };
  const close = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };
  const pick = (i: number) => {
    onChange(OPTIONS[i].value);
    close();
  };

  const onButtonKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      show();
    }
  };
  const onListKey = (e: KeyboardEvent) => {
    const last = OPTIONS.length - 1;
    const moves: Record<string, number> = {
      ArrowDown: Math.min(last, active + 1),
      ArrowUp: Math.max(0, active - 1),
      Home: 0,
      End: last,
    };
    if (e.key in moves) {
      e.preventDefault();
      setActive(moves[e.key]);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      pick(active);
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      e.preventDefault();
      close();
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-label="Reasoning level"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => (open ? close() : show())}
        onKeyDown={onButtonKey}
        className={cn(
          'flex items-center gap-1 rounded font-mono text-[10.5px] outline-none transition-colors hover:text-fg focus-visible:ring-1 focus-visible:ring-accent',
          value && 'text-accent-ink',
        )}
      >
        {OPTIONS[selectedIndex].label}
        <svg aria-hidden width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className={cn('transition-transform', open && 'rotate-180')}><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          tabIndex={-1}
          aria-label="Reasoning level"
          aria-activedescendant={`${listId}-${active}`}
          onKeyDown={onListKey}
          className="absolute bottom-full left-0 z-50 mb-1.5 min-w-[9.5rem] overflow-hidden rounded-md border border-line bg-panel py-1 shadow-panel outline-none"
        >
          {OPTIONS.map((o, i) => {
            const selected = i === selectedIndex;
            return (
              <li
                key={o.value || 'default'}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={selected}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(i)}
                className={cn(
                  'flex cursor-pointer items-center gap-2 px-2.5 py-1.5 font-mono text-[11px] md:py-1',
                  i === active ? 'bg-panel-2 text-fg' : 'text-fg-dim',
                  selected && 'text-accent-ink',
                )}
              >
                <span aria-hidden className="w-2.5">{selected ? '✓' : ''}</span>
                {o.label}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
