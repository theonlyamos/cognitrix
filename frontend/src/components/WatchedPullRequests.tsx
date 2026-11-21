import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { friendlyTime } from '@/lib/plain';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

/** A pull request an agent watches (cognitrix/tasks/pr_watch.py). */
export interface PullRequestWatch {
  id: string;
  repo: string;
  number: number;
  title: string;
  url: string;
  target: 'chat' | 'task';
  session_id: string | null;
  task_id: string | null;
  /** Where news goes, e.g. chat "Release prep". */
  where: string;
  mode: 'poll' | 'webhook' | 'app';
  check_seconds: number | null;
  next_check_at?: string | null;
  /** Why it is checked on a timer although webhooks were possible. */
  note: string | null;
  status: 'watching' | 'stopped';
  stop_reason: string | null;
  error: string | null;
  last_news_at: string | null;
}

function Chip({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim', className)}>{children}</span>;
}

function watchSpeed(w: PullRequestWatch): string {
  if (w.mode === 'webhook' || w.mode === 'app') return 'instant';
  const minutes = Math.max(1, Math.round((w.check_seconds ?? 120) / 60));
  return `every ${minutes} min`;
}

/** Pull requests agents watch: news wakes the chat or task that asked. */
export default function WatchedPullRequests({ watches, onChange }: { watches: PullRequestWatch[]; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const act = async (w: PullRequestWatch, how: 'stop' | 'remove') => {
    setBusy(w.id);
    setFailed(null);
    try {
      if (how === 'stop') await api.post(`/github/watches/${w.id}/stop`);
      else await api.delete(`/github/watches/${w.id}`);
      onChange();
    } catch (err) {
      setFailed(errorMessage(err, tr('ui.automations.couldNotChangeTheWatch')));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section aria-labelledby="watched-prs" className="border-b border-line px-4 py-4 md:px-6">
      <h2 id="watched-prs" className="text-[13px] font-medium">{tr('ui.automations.watchedPullRequests')}</h2>
      <p className="mt-1 text-[12.5px] text-fg-dim">
        {tr('ui.automations.agentsWatchingAGithubPull')}
      </p>
      {failed && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{failed}</p>}
      <ul className="mt-2 flex flex-col divide-y divide-line rounded-[10px] border border-line">
        {watches.map((w) => {
          const live = w.status === 'watching';
          const target = w.target === 'task' && w.task_id ? `/tasks/${w.task_id}` : w.session_id ? `/home?chat=${w.session_id}` : null;
          return (
            <li key={w.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2">
              <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', live ? 'bg-accent' : 'bg-fg-dim')} aria-hidden />
              <a href={w.url} target="_blank" rel="noreferrer" className="min-w-0 truncate text-[13px] font-medium hover:text-accent-ink">
                {w.repo}#{w.number}
                {w.title ? <span className="font-normal text-fg-dim"> · {w.title}</span> : null}
              </a>
              {live && <Chip>{watchSpeed(w)}</Chip>}
              <span className="flex-1" />
              {live ? (
                <Button size="sm" variant="ghost" onClick={() => act(w, 'stop')} disabled={busy !== null}>
                  {tr('ui.automations.stopWatching')}
                </Button>
              ) : (
                <Button size="sm" variant="ghost" className="hover:text-danger-ink" onClick={() => act(w, 'remove')} disabled={busy !== null}>
                  {tr('ui.automations.remove')}
                </Button>
              )}
              <div className="w-full pl-3.5 text-[12px] text-fg-dim">
                {w.where && (
                  <>
                    {tr('ui.automations.newsGoesTo')}{' '}
                    {target ? <Link to={target} className="underline-offset-2 hover:underline">{w.where}</Link> : w.where}
                    {'. '}
                  </>
                )}
                {!live && w.stop_reason ? tr('ui.automations.stoppedStopReason', { stop_reason: w.stop_reason }) : null}
                {live && w.last_news_at ? tr('ui.automations.lastNewsUtctolocal', { utcToLocal: friendlyTime(w.last_news_at) }) : null}
                {live && w.mode === 'poll' && w.next_check_at ? <>{' '}{tr('ui.automations.nextCheckUtctolocal', { utcToLocal: friendlyTime(w.next_check_at) })}</> : null}
              </div>
              {live && w.note && <p className="w-full pl-3.5 text-[12px] text-fg-dim">{w.note}</p>}
              {live && w.error && <p role="alert" className="w-full pl-3.5 text-[12px] text-danger-ink">{w.error}</p>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

