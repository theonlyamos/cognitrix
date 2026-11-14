import { useEffect, useRef, useState } from 'react';
import { AppWindow, Lock, Users } from 'lucide-react';
import { APP_FRAME_SANDBOX, STATUS_LABEL, type AppAccess, type AppStatus, type AppSummary } from '@/lib/hostedApps';
import { cn } from '@/lib/utils';

const DOT: Record<AppStatus, string> = {
  online: 'bg-accent shadow-[0_0_0_3px_rgb(var(--accent-rgb)/0.22)]',
  asleep: 'border-[1.5px] border-fg-dim bg-transparent',
  building: 'bg-warn animate-pulse',
  failed: 'bg-danger-ink',
  stopped: 'border-[1.5px] border-fg-dim bg-transparent',
};
const TEXT: Record<AppStatus, string> = {
  online: 'text-accent-ink', asleep: 'text-fg-dim', building: 'text-warn-ink',
  failed: 'text-danger-ink', stopped: 'text-fg-dim',
};

/** "Online · v7"; a failed update with an older version still live says so. */
export function AppStatusBadge({ app, className }: { app: Pick<AppSummary, 'status' | 'version' | 'latest'>; className?: string }) {
  const failedUpdate = app.version > 0 && app.latest > app.version && app.status === 'online';
  const status: AppStatus = app.status;
  return (
    <span className={cn('inline-flex items-center gap-1.5 whitespace-nowrap font-mono text-[11px]', TEXT[status], className)}>
      <span className={cn('h-[7px] w-[7px] flex-none rounded-full', DOT[status])} aria-hidden />
      {STATUS_LABEL[status]}{app.version > 0 && status !== 'building' ? ` · v${app.version}` : ''}
      {failedUpdate && <span className="text-danger-ink">· v{app.latest} failed</span>}
    </span>
  );
}

export function AccessLabel({ access }: { access: AppAccess }) {
  const Icon = access === 'private' ? Lock : Users;
  const label = access === 'workspace' ? 'Everyone on this server' : access === 'people' ? 'Shared with people' : 'Only you';
  return <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[12px] text-fg-dim"><Icon size={13} aria-hidden />{label}</span>;
}

export function Sparkline({ values, width = 90, height = 24, label }: { values: number[]; width?: number; height?: number; label?: string }) {
  const max = Math.max(1, ...values);
  const points = values.map((v, i) => `${(i * (width - 4) / Math.max(1, values.length - 1) + 2).toFixed(1)},${(height - 3 - (v / max) * (height - 6)).toFixed(1)}`).join(' ');
  const total = values.reduce((a, b) => a + b, 0);
  return (
    <svg width={width} height={height} role="img" aria-label={label ?? `${total} visits in the last ${values.length} days`} className="flex-none overflow-visible">
      <polyline points={points} fill="none" className="stroke-accent-ink" strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/** A small live view of a running app: its real page, scaled down, not clickable. */
export function AppThumbnail({ app, className }: { app: AppSummary; className?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.25);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const watch = new ResizeObserver(() => setScale(el.clientWidth / 1280));
    watch.observe(el);
    setScale(el.clientWidth / 1280);
    return () => watch.disconnect();
  }, []);
  const live = app.status === 'online' && app.thumb_url;
  return (
    <div ref={box} className={cn('relative overflow-hidden bg-panel-2', className)} aria-hidden>
      {live ? (
        <iframe src={app.thumb_url} title="" tabIndex={-1} loading="lazy" sandbox={APP_FRAME_SANDBOX}
          className="pointer-events-none absolute left-0 top-0 origin-top-left border-0 bg-white"
          style={{ width: 1280, height: 800, transform: `scale(${scale})` }} />
      ) : (
        <div className={cn('absolute inset-0 grid place-items-center', app.status === 'building' ? 'text-warn-ink' : 'text-fg-dim/50')}>
          {app.status === 'building'
            ? <span className="font-mono text-[12px]">building v{Math.max(1, app.latest)}…</span>
            : <AppWindow size={28} strokeWidth={1.5} />}
        </div>
      )}
    </div>
  );
}
