import { Link } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { usePolling } from '@/hooks/usePolling';
import { Button } from '@/lib/components/ui/button';
import { PageHeader, LoadingState, ErrorState, EmptyState, Chevron } from '@/components/list-ui';
import type { TaskTriggerRow } from '@/components/AutomationPanel';
import { utcToLocal } from '@/lib/time';
import { cn } from '@/lib/utils';

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
  usePolling(() => refetch({ silent: true }), 15000, true);
  const items = data ?? [];

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title="Automations" subtitle="Tasks that run on their own: on a schedule, when the agent checks back, or when a webhook fires">
        <Button asChild size="sm">
          <Link to="/tasks/new">+ New task</Link>
        </Button>
      </PageHeader>

      <div className="flex-1 overflow-y-auto">
        {loading && !data ? (
          <LoadingState label="loading automations…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<AutomationIcon />}
            title="No automations yet"
            desc="Give a task a schedule, notes, or a webhook trigger and it shows up here. Agents in those runs can keep notes, pick their next check, and notify you."
            action={
              <Button asChild>
                <Link to="/tasks">Open tasks</Link>
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
                        {schedule && <Chip className={a.schedule_enabled ? '' : 'opacity-60'}>⏱ {schedule}{a.schedule_enabled ? '' : ' · paused'}</Chip>}
                        {a.wake_pending && <Chip>↺ self-check</Chip>}
                        {a.triggers.length > 0 && (
                          <Chip>⚡ {enabledTriggers}/{a.triggers.length} webhook{a.triggers.length === 1 ? '' : 's'}</Chip>
                        )}
                        {a.pending_events > 0 && <Chip className="text-accent-ink">{a.pending_events} event{a.pending_events === 1 ? '' : 's'} waiting</Chip>}
                        {a.notes && <Chip>notes</Chip>}
                      </div>
                      {a.wake_pending && a.wake_reason && (
                        <div className="mt-1 truncate text-[12.5px] text-fg-dim">Next check: {a.wake_reason}</div>
                      )}
                      {a.last_run?.result && (
                        <div className="mt-1 truncate text-[12.5px] text-fg-dim">Last result: {a.last_run.result}</div>
                      )}
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="hidden text-right sm:block">
                        {next && <div className="font-mono text-[10.5px] text-fg-dim">next {next}</div>}
                        {a.last_run && (
                          <div className={cn('font-mono text-[10.5px]', RUN_STATUS[a.last_run.status] || 'text-fg-dim')}>
                            last {a.last_run.status}
                            {a.last_run.completed_at ? ` · ${utcToLocal(a.last_run.completed_at)}` : ''}
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

function AutomationIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 4v5h-5" /><path d="M12 7v5l3 2" />
    </svg>
  );
}
