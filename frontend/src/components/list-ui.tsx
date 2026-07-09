import type { ReactNode } from 'react';

export function Spinner({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" className="opacity-25" />
      <path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  );
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle: string; children?: ReactNode }) {
  return (
    <header className="app-page-header flex flex-none flex-col items-stretch gap-4 border-b border-line py-4 pl-16 pr-4 sm:flex-row sm:items-center md:px-6">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-0.5 font-mono text-[11px] tracking-[0.02em] text-fg-dim">{subtitle}</p>
      </div>
      {children && <div className="flex flex-wrap items-center gap-2 sm:ml-auto">{children}</div>}
    </header>
  );
}

export function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center gap-2 font-mono text-sm text-fg-dim">
      <Spinner className="h-4 w-4 text-accent" /> {label}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center text-center">
      <div className="border-l-2 border-danger bg-danger/5 px-4 py-3 text-left font-mono text-[12px] text-danger-ink">{message}</div>
      <button
        onClick={onRetry}
        className="mt-4 rounded border border-line px-4 py-2 font-mono text-[12px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg"
      >
        ↻ retry
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
