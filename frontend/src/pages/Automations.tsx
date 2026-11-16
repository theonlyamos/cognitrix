import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { usePolling } from '@/hooks/usePolling';
import { Button } from '@/lib/components/ui/button';
import { PageHeader, LoadingState, ErrorState, EmptyState, Chevron } from '@/components/list-ui';
import type { TaskTriggerRow } from '@/components/AutomationPanel';
import { utcToLocal } from '@/lib/time';
import { cn } from '@/lib/utils';
import { tr, trn } from '@/lib/i18n';

interface Automation {
  id: string;
  title: string;
  schedule_at: string | null;
  schedule_interval: number | null;
  schedule_cron: string | null;
  schedule_enabled: boolean;
  next_run_at: string | null;
  wake_pending: boolean;
  wake_at: string | null;
  wake_reason: string | null;
  notes: string;
  triggers: TaskTriggerRow[];
  pending_events: number;
  last_run: {
    id: string;
    status: string;
    trigger_kind: string | null;
    completed_at: string | null;
    result: string;
  } | null;
}

/** A pull request an agent watches (cognitrix/tasks/pr_watch.py). */
interface PullRequestWatch {
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
  mode: 'poll' | 'webhook';
  check_seconds: number | null;
  /** Why it is checked on a timer although webhooks were possible. */
  note: string | null;
  status: 'watching' | 'stopped';
  stop_reason: string | null;
  error: string | null;
  last_news_at: string | null;
}

const RUN_STATUS: Record<string, string> = {
  completed: 'text-ok',
  failed: 'text-danger-ink',
  cancelled: 'text-fg-dim',
  running: 'text-accent-ink',
  queued: 'text-accent-ink',
};

function every(seconds: number): string {
  if (seconds % 86400 === 0) return `every ${seconds / 86400}d`;
  if (seconds % 3600 === 0) return `every ${seconds / 3600}h`;
  if (seconds % 60 === 0) return `every ${seconds / 60}m`;
  return `every ${seconds}s`;
}

function scheduleLabel(a: Automation): string | null {
  if (a.schedule_cron) return `cron ${a.schedule_cron}`;
  if (a.schedule_interval) return every(a.schedule_interval);
  if (a.schedule_at) return `once ${utcToLocal(a.schedule_at)}`;
  return null;
}

/** Next thing that will start a run: the schedule or the agent's own check. */
function nextLabel(a: Automation): string | null {
  const times = [
    a.schedule_enabled && a.next_run_at ? a.next_run_at : null,
    a.wake_pending && a.wake_at ? a.wake_at : null,
  ].filter(Boolean) as string[];
  if (!times.length) return null;
  return utcToLocal(times.sort()[0]);
}

function Chip({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim', className)}>{children}</span>;
}

/** Every task that runs on its own: scheduled, self-checking, or triggered by webhooks. */
export default function Automations() {
  const { data, loading, error, refetch } = useResource<Automation[]>('/automations');
  const watched = useResource<PullRequestWatch[]>('/github/watches');
  usePolling(() => {
    refetch({ silent: true });
    watched.refetch({ silent: true });
  }, 15000, true);
  const items = data ?? [];
  const watches = watched.data ?? [];

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.automations.automations')} subtitle={tr('ui.automations.tasksThatRunOnTheir')}>
        <Button asChild size="sm">
          <Link to="/tasks/new">{tr('ui.automations.newTask')}</Link>
        </Button>
      </PageHeader>

      <div className="flex-1 overflow-y-auto">
        {watches.length > 0 && <WatchedPullRequests watches={watches} onChange={() => watched.refetch({ silent: true })} />}
        {loading && !data ? (
          <LoadingState label={tr('ui.automations.loadingAutomations')} />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : items.length === 0 && watches.length === 0 ? (
          <EmptyState
            icon={<AutomationIcon />}
            title={tr('ui.automations.noAutomationsYet')}
            desc={tr('ui.automations.giveATaskASchedule')}
            action={
              <Button asChild>
                <Link to="/tasks">{tr('ui.automations.openTasks')}</Link>
              </Button>
            }
          />
        ) : (
          <ul>
            {items.map((a) => {
              const schedule = scheduleLabel(a);
              const next = nextLabel(a);
              const enabledTriggers = a.triggers.filter((t) => t.enabled).length;
              return (
                <li key={a.id}>
                  <Link
                    to={`/tasks/${a.id}?automation=1`}
                    className="group grid grid-cols-[1fr_auto] items-center gap-4 border-b border-line px-6 py-3.5 transition-colors hover:bg-panel-2 max-md:px-4"
                  >
                    <div className="min-w-0">
                      <div className="truncate font-medium transition-colors group-hover:text-accent-ink">{a.title}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        {schedule && <Chip className={a.schedule_enabled ? '' : 'opacity-60'}>⏱ {schedule}{a.schedule_enabled ? '' : tr('ui.automations.paused')}</Chip>}
                        {a.wake_pending && <Chip>{tr('ui.automations.selfCheck')}</Chip>}
                        {a.triggers.length > 0 && (
                          <Chip>⚡ {trn(a.triggers.length, 'ui.automations.webhooks_one', 'ui.automations.webhooks_other', { enabled: enabledTriggers })}</Chip>
                        )}
                        {a.pending_events > 0 && <Chip className="text-accent-ink">{trn(a.pending_events, 'ui.automations.eventsWaiting_one', 'ui.automations.eventsWaiting_other')}</Chip>}
                        {a.notes && <Chip>{tr('ui.automations.notes')}</Chip>}
                      </div>
                      {a.wake_pending && a.wake_reason && (
                        <div className="mt-1 truncate text-[12.5px] text-fg-dim">{tr('ui.automations.nextCheckWakeReason', { wake_reason: a.wake_reason })}</div>
                      )}
                      {a.last_run?.result && (
                        <div className="mt-1 truncate text-[12.5px] text-fg-dim">{tr('ui.automations.lastResultResult', { result: a.last_run.result })}</div>
                      )}
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="hidden text-right sm:block">
                        {next && <div className="font-mono text-[10.5px] text-fg-dim">{tr('ui.automations.nextNext', { next })}</div>}
                        {a.last_run && (
                          <div className={cn('font-mono text-[10.5px]', RUN_STATUS[a.last_run.status] || 'text-fg-dim')}>
                            {tr('ui.automations.lastStatusValue', { status: a.last_run.status, value: a.last_run.completed_at ? ` · ${utcToLocal(a.last_run.completed_at)}` : '' })}
                          </div>
                        )}
                      </div>
                      <Chevron />
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

function watchSpeed(w: PullRequestWatch): string {
  if (w.mode === 'webhook') return 'instant';
  const minutes = Math.max(1, Math.round((w.check_seconds ?? 120) / 60));
  return `every ${minutes} min`;
}

/** Pull requests agents watch: news wakes the chat or task that asked. */
function WatchedPullRequests({ watches, onChange }: { watches: PullRequestWatch[]; onChange: () => void }) {
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
    <section aria-labelledby="watched-prs" className="border-b border-line px-6 py-4 max-md:px-4">
      <h2 id="watched-prs" className="font-mono text-[10px] tracking-[0.18em] text-fg-dim">{tr('ui.automations.watchedPullRequests')}</h2>
      <p className="mt-1 text-[12.5px] text-fg-dim">
        {tr('ui.automations.agentsWatchingAGithubPull')}
      </p>
      {failed && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{failed}</p>}
      <ul className="mt-2 flex flex-col divide-y divide-line rounded-[10px] border border-line">
        {watches.map((w) => {
          const live = w.status === 'watching';
          const target = w.target === 'task' && w.task_id ? `/tasks/${w.task_id}?automation=1` : w.session_id ? `/home?chat=${w.session_id}` : null;
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
                {live && w.last_news_at ? tr('ui.automations.lastNewsUtctolocal', { utcToLocal: utcToLocal(w.last_news_at) }) : null}
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

function AutomationIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 4v5h-5" /><path d="M12 7v5l3 2" />
    </svg>
  );
}
