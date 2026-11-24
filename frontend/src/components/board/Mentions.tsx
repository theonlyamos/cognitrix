import { Fragment, useMemo, useRef, useState, type KeyboardEvent, type TextareaHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** The @word being typed just before the caret, if any. */
function typedMention(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const match = /(^|[^\w@])@([\w-]*(?: [\w-]*)?)$/.exec(before);
  if (!match) return null;
  return { start: caret - match[2].length - 1, query: match[2] };
}

/**
 * A textarea that offers agent names after "@" (arrows to move, Enter or Tab
 * to pick, Escape to close).
 */
export function MentionTextarea({
  value, onValue, names, className, ...rest
}: Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange'> & {
  value: string;
  onValue: (value: string) => void;
  names: string[];
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [at, setAt] = useState<{ start: number; query: string } | null>(null);
  const [active, setActive] = useState(0);
  const options = useMemo(() => {
    if (!at) return [];
    const q = at.query.toLowerCase();
    return names.filter((n) => n.toLowerCase().startsWith(q) || n.toLowerCase().replace(/ /g, '').startsWith(q.replace(/ /g, '')))
      .slice(0, 6);
  }, [at, names]);
  const open = options.length > 0;

  const look = (text: string, caret: number) => {
    setAt(typedMention(text, caret));
    setActive(0);
  };
  const pick = (name: string) => {
    if (!at) return;
    const end = at.start + 1 + at.query.length;
    const next = `${value.slice(0, at.start)}@${name} ${value.slice(end)}`;
    onValue(next);
    setAt(null);
    const caret = at.start + name.length + 2;
    requestAnimationFrame(() => {
      ref.current?.focus();
      ref.current?.setSelectionRange(caret, caret);
    });
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!open) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((i) => (i + (event.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length);
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      pick(options[active]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setAt(null);
    }
  };

  return (
    <div className="relative">
      <textarea
        {...rest}
        ref={ref}
        value={value}
        onChange={(e) => { onValue(e.target.value); look(e.target.value, e.target.selectionStart ?? e.target.value.length); }}
        onKeyDown={onKeyDown}
        onBlur={() => setTimeout(() => setAt(null), 120)}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        aria-controls={open ? 'board-mention-list' : undefined}
        className={className}
      />
      {open && (
        <ul id="board-mention-list" role="listbox" aria-label={tr('ui.board.tagAnAgent')}
          className="absolute left-0 top-full z-20 mt-1 min-w-[180px] overflow-hidden rounded-lg border border-line bg-panel py-1 text-[13px] shadow-lg">
          {options.map((name, i) => (
            <li key={name} role="option" aria-selected={i === active}
              onMouseDown={(e) => { e.preventDefault(); pick(name); }}
              className={cn('cursor-pointer px-3 py-1.5', i === active ? 'bg-panel-2 text-fg' : 'text-fg-dim')}>
              @{name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Text with each @Name of a tagged agent drawn as a tag. */
export function MentionText({ text, names }: { text: string; names: string[] }) {
  const parts = useMemo(() => {
    const variants = names.flatMap((n) => [n, n.replace(/ /g, '_'), n.replace(/ /g, '-'), n.replace(/ /g, '')])
      .filter(Boolean).sort((a, b) => b.length - a.length);
    if (!variants.length) return [text];
    const pattern = new RegExp(`(?<![\\w@])(@(?:${[...new Set(variants)].map(escape).join('|')}))(?![\\w-])`, 'gi');
    return text.split(pattern);
  }, [text, names]);
  return (
    <>
      {parts.map((part, i) => (i % 2 === 1
        ? <span key={i} className="rounded bg-accent/15 px-0.5 font-medium text-accent-ink">{part}</span>
        : <Fragment key={i}>{part}</Fragment>))}
    </>
  );
}
