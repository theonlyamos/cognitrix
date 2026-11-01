import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** Title and one-line description at the top of a settings section. */
export function SectionHeader({ title, description }: { title: string; description?: ReactNode }) {
  return (
    <header className="mb-4">
      <h2 className="text-[17px] font-semibold">{title}</h2>
      {description && <p className="mt-1 max-w-[62ch] text-[13px] leading-relaxed text-fg-dim">{description}</p>}
    </header>
  );
}

/** Small label above a group of rows inside a section. */
export function SubHeading({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={cn('mb-2 mt-6 font-mono text-[10.5px] tracking-[0.14em] text-fg-dim first:mt-0', className)}>{children}</h3>;
}

/** A bordered list of setting rows. */
export function Group({ children, className, danger }: { children: ReactNode; className?: string; danger?: boolean }) {
  return (
    <div className={cn('divide-y divide-line rounded-[12px] border bg-panel', danger ? 'border-danger/35' : 'border-line', className)}>
      {children}
    </div>
  );
}

/** One setting: label and detail on the left, control on the right.
 *  On phones the control drops under the text when it doesn't fit. */
export function SettingRow({
  label, detail, children, lead, className,
}: {
  label: ReactNode;
  detail?: ReactNode;
  children?: ReactNode;
  lead?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-wrap items-center gap-x-3 gap-y-2 px-3.5 py-3', className)}>
      {lead}
      <div className="min-w-0 flex-1 basis-[180px]">
        <div className="text-[13.5px] font-medium">{label}</div>
        {detail && <div className="mt-0.5 text-[12px] leading-snug text-fg-dim">{detail}</div>}
      </div>
      {children && <div className="flex flex-none flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

export function Segmented<T extends string>({
  label, value, options, onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex gap-0.5 rounded-lg border border-line p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            'h-8 rounded-md px-2.5 text-[12.5px] md:h-7',
            value === o.value ? 'bg-panel-2 font-medium text-fg' : 'text-fg-dim hover:text-fg',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const buttonClass = 'inline-flex h-9 items-center rounded-lg border border-line px-3 text-[12.5px] hover:bg-panel-2 disabled:opacity-60 md:h-8';
export const quietButtonClass = 'inline-flex h-9 items-center rounded-lg px-2.5 text-[12.5px] text-fg-dim hover:bg-panel-2 hover:text-fg disabled:opacity-60 md:h-8';
export const primaryButtonClass = 'inline-flex h-9 items-center rounded-lg bg-accent px-3 text-[12.5px] font-medium text-[#0b0c0e] disabled:opacity-60 md:h-8';

export function StatusPill({ tone, children }: { tone: 'ok' | 'warn' | 'off' | 'accent'; children: ReactNode }) {
  const color = { ok: 'text-ok', warn: 'text-[#a15c00] dark:text-[#e5b567]', off: 'text-fg-dim', accent: 'text-accent-ink' }[tone];
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-full border border-line px-2 py-0.5 font-mono text-[10.5px] tracking-[0.04em]', color)}>
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}
