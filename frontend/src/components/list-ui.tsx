import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Search } from 'lucide-react';
import { Button } from '@/lib/components/ui/button';
import { statusLabel, statusTone } from '@/lib/plain';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

export function Spinner({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" className="opacity-25" />
      <path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  );
}

/**
 * The top of every list and detail page: an optional trail back to where the
 * page hangs ("Agents › Writer"), the title, a plain sentence, and the page's
 * buttons on the right.
 */
export function PageHeader({ title, subtitle, trail, children }: {
  title: string;
  subtitle?: string;
  /** Parent pages, nearest last: [{ label: 'Agents', to: '/agents' }]. */
  trail?: { label: string; to: string }[];
  children?: ReactNode;
}) {
  return (
    <header className="app-page-header flex min-h-14 flex-none flex-row flex-nowrap items-center gap-2 border-b border-line py-2 pl-16 pr-4 md:px-6">
      <div className="min-w-0 flex-1 overflow-hidden">
        {trail && trail.length > 0 && (
          <nav aria-label={tr('ui.listUi.youAreHere')} className="flex items-center gap-1.5 truncate text-[12.5px] text-fg-dim">
            {trail.map((step) => (
              <span key={step.to} className="flex items-center gap-1.5">
                <Link to={step.to} className="hover:text-fg hover:underline">{step.label}</Link>
                <span aria-hidden>›</span>
              </span>
            ))}
          </nav>
        )}
        <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="sr-only mt-0.5 truncate text-[12.5px] text-fg-dim md:not-sr-only">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-none items-center gap-1 md:ml-auto md:gap-2">{children}</div>}
    </header>
  );
}

/** The trail on its own, for pages that draw their own header. */
export function Trail({ steps, current }: { steps: { label: string; to: string }[]; current?: string }) {
  return (
    <nav aria-label={tr('ui.listUi.youAreHere')} className="flex min-w-0 items-center gap-1.5 text-[13px] text-fg-dim">
      {steps.map((step) => (
        <span key={step.to} className="flex flex-none items-center gap-1.5">
          <Link to={step.to} className="hover:text-fg hover:underline">{step.label}</Link>
          <span aria-hidden>›</span>
        </span>
      ))}
      {current && <span aria-current="page" className="truncate text-fg">{current}</span>}
    </nav>
  );
}

/** "New agent", "New task": the one main button a list page has. */
export function NewButton({ to, label }: { to: string; label: string }) {
  return (
    <Button asChild size="sm">
      <Link to={to}><Plus size={15} strokeWidth={2.2} aria-hidden className="mr-1.5 flex-none" />{label}</Link>
    </Button>
  );
}

export interface FilterOption<T extends string> { id: T; label: string; count?: number }

/** A row of filters above a list; one is always picked. */
export function FilterChips<T extends string>({ options, value, onChange, label }: {
  options: FilterOption<T>[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex min-w-0 flex-wrap items-center gap-1.5">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          role="tab"
          aria-selected={value === option.id}
          onClick={() => onChange(option.id)}
          className={cn(
            'inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors md:min-h-8',
            value === option.id ? 'border-fg bg-fg font-medium text-bg' : 'border-line text-fg-dim hover:border-fg-dim hover:text-fg',
          )}
        >
          {option.label}
          {option.count !== undefined && option.count > 0 && <span className="font-mono text-[11px] opacity-70">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** The search box of a list page. */
export function ListSearch({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <label className="relative block w-full sm:w-64">
      <Search size={14} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-dim" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="h-10 w-full rounded-lg border border-line bg-panel pl-8 pr-3 text-[13.5px] outline-none placeholder:text-fg-dim focus:border-fg-dim md:h-8"
      />
    </label>
  );
}

/** Filters on the left, search on the right, under a page header. */
export function ListToolbar({ children }: { children: ReactNode }) {
  return <div className="flex flex-none flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5 md:px-6">{children}</div>;
}

/** A status in words, e.g. "Running": see lib/plain. */
export function StatusPill({ status, label }: { status: string | null | undefined; label?: string }) {
  return (
    <span className={cn('flex-none whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[12px]', statusTone(status))}>
      {label ?? statusLabel(status)}
    </span>
  );
}

export function LoadingState({ label }: { label: string }) {
  return (
    <div role="status" className="flex h-full items-center justify-center gap-2 font-mono text-sm text-fg-dim">
      <Spinner className="h-4 w-4 text-accent" /> {label}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="mx-auto flex h-full max-w-md flex-col items-center justify-center text-center">
      <div className="border-l-2 border-danger bg-danger/5 px-4 py-3 text-left font-mono text-[12px] text-danger-ink">{message}</div>
      <button
        onClick={onRetry}
        className="mt-4 min-h-11 rounded border border-line px-4 py-2 font-mono text-[12px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:min-h-0"
      >
        {tr('ui.listUi.retry')}
      </button>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  desc,
  action,
}: {
  icon: ReactNode;
  title: string;
  desc: string;
  action?: ReactNode;
}) {
  return (
    <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center px-6 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-md border border-line text-fg-dim">{icon}</div>
      <h3 className="mt-4 text-lg font-semibold">{title}</h3>
      <p className="mt-1.5 text-sm text-fg-dim">{desc}</p>
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}

export function Chevron() {
  return (
    <svg className="h-4 w-4 flex-none text-fg-dim transition-colors group-hover:text-accent-ink" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}
