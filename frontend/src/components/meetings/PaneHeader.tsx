import type { ReactNode } from 'react';
import { ChevronLeft } from 'lucide-react';
import { tr } from '@/lib/i18n';

/** The top of the meeting pane: title, a status badge, one meta line, and
 *  the meeting's buttons. On a phone it also has a way back to the list. */
export function PaneHeader({ title, badge, meta, actions, onBack }: {
  title: string; badge?: ReactNode; meta?: ReactNode; actions?: ReactNode; onBack: () => void;
}) {
  return (
    <div className="flex flex-none flex-wrap items-start gap-x-3 gap-y-2 border-b border-line px-3 py-3 md:px-6 md:py-4">
      <button type="button" onClick={onBack} aria-label={tr('ui.meetings.paneHeader.backToMeetings')}
        className="-ml-1 flex h-10 w-9 flex-none items-center justify-center rounded-lg text-fg-dim hover:bg-panel-2 hover:text-fg md:hidden">
        <ChevronLeft size={20} aria-hidden />
      </button>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
          <h2 className="min-w-0 truncate text-[17px] font-semibold tracking-tight md:text-[19px]">{title}</h2>
          {badge}
        </div>
        {meta && <div className="mt-0.5 text-[12.5px] text-fg-dim">{meta}</div>}
      </div>
      {actions && <div className="flex w-full flex-none flex-wrap items-center gap-1.5 wide:w-auto">{actions}</div>}
    </div>
  );
}

/** A small rounded label: live, a question waiting, failed… */
export function Badge({ tone, children, pulse }: { tone: 'live' | 'warn' | 'fail' | 'plain' | 'ok'; children: ReactNode; pulse?: boolean }) {
  const cls = {
    live: 'bg-accent text-accent-foreground',
    warn: 'bg-warn/15 text-warn-ink',
    fail: 'bg-danger/10 text-danger-ink',
    plain: 'bg-panel-2 text-fg-dim',
    ok: 'bg-accent-ink/10 text-accent-ink',
  }[tone];
  return (
    <span className={`inline-flex h-[22px] flex-none items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-[11.5px] font-medium ${cls}`}>
      {pulse !== undefined && <span className={`h-1.5 w-1.5 rounded-full bg-current ${pulse ? 'animate-pulse' : ''}`} aria-hidden />}
      {children}
    </span>
  );
}
