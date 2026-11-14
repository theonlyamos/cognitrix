import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface PanelField { label: string; value: ReactNode }

interface PanelCardProps {
  /** Short mono label in the corner: "APPROVAL", "QUESTION", "ALERT". */
  tag: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  tone?: 'accent' | 'danger';
  fields?: PanelField[];
  children?: ReactNode;
  footer?: ReactNode;
  className?: string;
  role?: string;
  labelledBy?: string;
  titleId?: string;
}

/** Style A: a bordered panel with a header, a row of facts, a body and a
 *  footer of actions. Used for things that wait on the person: approvals,
 *  questions and alerts. */
export function PanelCard({
  tag, title, subtitle, tone = 'accent', fields, children, footer, className, role, labelledBy, titleId,
}: PanelCardProps) {
  return (
    <section
      role={role}
      aria-labelledby={labelledBy}
      data-tone={tone}
      className={cn(
        'animate-rise overflow-hidden rounded-xl border bg-panel',
        tone === 'danger' ? 'border-danger/40' : 'border-line',
        className,
      )}
    >
      <div className="flex items-start gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="text-[15px] font-semibold leading-snug text-fg">{title}</h2>
          {subtitle && <div className="mt-0.5 text-[12.5px] leading-relaxed text-fg-dim">{subtitle}</div>}
        </div>
        <span
          className={cn(
            'flex-none rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em]',
            tone === 'danger' ? 'border-danger/40 text-danger-ink' : 'border-line text-fg-dim',
          )}
        >
          {tag}
        </span>
      </div>
      {fields && fields.length > 0 && (
        <div className="grid grid-cols-2 border-b border-line sm:auto-cols-fr sm:grid-flow-col sm:grid-cols-none">
          {fields.map((f, i) => (
            <div key={i} className="min-w-0 border-line px-4 py-2.5 [&:not(:last-child)]:border-r">
              <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-fg-dim">{f.label}</div>
              <div className="mt-0.5 truncate text-[14px] font-medium">{f.value}</div>
            </div>
          ))}
        </div>
      )}
      {children && <div className="px-4 py-3">{children}</div>}
      {footer && <div className="flex flex-wrap items-center gap-2 border-t border-line bg-bg px-4 py-3">{footer}</div>}
    </section>
  );
}

/** A footer button in the panel's style. */
export function PanelButton({
  children, onClick, kind = 'default', disabled, title, type = 'button',
}: {
  children: ReactNode;
  onClick?: () => void;
  kind?: 'primary' | 'default' | 'danger' | 'quiet';
  disabled?: boolean;
  title?: string;
  type?: 'button' | 'submit';
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        'min-h-11 rounded-md border px-3 py-1.5 text-[13px] font-medium transition md:min-h-0 disabled:cursor-not-allowed disabled:opacity-50',
        kind === 'primary' && 'border-transparent bg-accent text-accent-foreground hover:brightness-105',
        kind === 'default' && 'border-line bg-panel text-fg hover:border-fg-dim',
        kind === 'danger' && 'border-line bg-panel text-fg hover:border-danger hover:text-danger-ink',
        kind === 'quiet' && 'border-transparent bg-transparent text-fg-dim hover:text-danger-ink',
      )}
    >
      {children}
    </button>
  );
}
