import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

// ── Small layout pieces matching the drafts ─────────────────────────────

export function Card({ title, description, aside, children, className, tone }: {
  title?: ReactNode;
  description?: ReactNode;
  aside?: ReactNode;
  children?: ReactNode;
  className?: string;
  tone?: 'warn' | 'danger';
}) {
  return (
    <section className={cn(
      'rounded-[10px] border bg-panel px-4 py-4 md:px-[22px] md:py-5',
      tone === 'warn' ? 'border-[#a15c00]/60 dark:border-[#e5b567]/60' : tone === 'danger' ? 'border-danger/40' : 'border-line',
      className,
    )}>
      {(title || aside) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {title && <h2 className="text-[16px] font-semibold md:text-[17px]">{title}</h2>}
          {description && <p className="text-[13px] text-fg-dim">{description}</p>}
          {aside && <div className="ml-auto flex flex-wrap items-center gap-2">{aside}</div>}
        </div>
      )}
      {children && <div className={cn(title || aside ? 'mt-4' : '')}>{children}</div>}
    </section>
  );
}

export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('font-mono text-[11px] tracking-[0.12em] text-fg-dim', className)}>{children}</div>;
}

export function Check({ checked, onChange, label, detail, disabled }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  detail?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={cn('flex gap-3', disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer')}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)}
        className="mt-[3px] h-4 w-4 flex-none accent-[var(--accent)]" />
      <span className="min-w-0">
        <span className="block text-[14.5px] font-medium">{label}</span>
        {detail && <span className="mt-0.5 block text-[13px] leading-relaxed text-fg-dim">{detail}</span>}
      </span>
    </label>
  );
}

export function SettingLine({ label, detail, children }: { label: ReactNode; detail?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="min-w-[220px] flex-1">
        <div className="text-[14.5px] font-medium">{label}</div>
        {detail && <div className="mt-0.5 text-[13px] text-fg-dim">{detail}</div>}
      </div>
      <div className="flex flex-none flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

export const inputClass = 'h-11 rounded border border-line bg-bg px-3 text-[14px] text-fg placeholder:text-fg-dim focus:border-accent disabled:opacity-50 md:h-10';
export const buttonClass = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-md border border-line px-3.5 text-[13px] text-fg transition-colors hover:bg-panel-2 disabled:opacity-60 md:min-h-9';
export const primaryButtonClass = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-md border border-accent bg-accent px-4 text-[13px] font-semibold text-[#0b0c0e] transition hover:brightness-105 disabled:opacity-60 md:min-h-9';
export const linkButtonClass = 'text-[13px] text-accent-ink hover:underline';

export function Pill({ children, tone = 'dim' }: { children: ReactNode; tone?: 'accent' | 'dim' | 'ok' | 'warn' | 'danger' | 'blue' }) {
  const color = {
    accent: 'border-accent-ink text-accent-ink',
    dim: 'border-line text-fg-dim',
    ok: 'border-ok/60 text-ok',
    warn: 'border-[#a15c00]/60 text-[#a15c00] dark:border-[#e5b567]/60 dark:text-[#e5b567]',
    danger: 'border-danger/60 text-danger-ink',
    blue: 'border-[var(--syn-alt)] text-[var(--syn-alt)]',
  }[tone];
  return <span className={cn('inline-flex items-center rounded-full border px-2 py-px font-mono text-[10.5px] font-medium', color)}>{children}</span>;
}

