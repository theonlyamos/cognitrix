import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { usePolling } from '@/hooks/usePolling';
import { useOptionalNotifications } from '@/context/NotificationsContext';
import { Button } from '@/lib/components/ui/button';
import {
  PageHeader, LoadingState, ErrorState, EmptyState, Chevron, NewButton, FilterChips, ListSearch, ListToolbar, StatusPill,
} from '@/components/list-ui';
import { AgentBadge } from '@/components/StartChat';
import WatchedPullRequests, { type PullRequestWatch } from '@/components/WatchedPullRequests';
import { friendlyTime, relativeTime, scheduleInWords } from '@/lib/plain';
import type { AgentAvatar } from '@/lib/agentAvatar';
import { tr } from '@/lib/i18n';

interface Task {
  id: string;
  title: string;
  description?: string;
  status?: string;
  done?: boolean;
  assigned_agents?: string[];
  team_id?: string | null;
  next_run_at?: string | null;
  schedule_enabled?: boolean;
  schedule_cron?: string | null;
  schedule_interval?: number | null;
  schedule_at?: string | null;
  wake_pending?: boolean;
  wake_at?: string | null;
  /** Set on the task behind an agent's check-ins; those live on the agent's page. */
  standing_agent_id?: string | null;
  run_status?: string | null;
  run_completed_at?: string | null;
}

interface AgentRow { id: string; name: string; avatar?: AgentAvatar | null }

type Show = 'all' | 'running' | 'scheduled' | 'needs' | 'done';
const SHOWS: Show[] = ['all', 'running', 'scheduled', 'needs', 'done'];

const isRunning = (t: Task) => ['in_progress', 'running', 'queued'].includes(String(t.run_status || t.status));
const isScheduled = (t: Task) => Boolean((t.schedule_enabled && (t.schedule_cron || t.schedule_interval || t.schedule_at)) || t.wake_pending);
const isDone = (t: Task) => !isRunning(t) && (t.done || ['completed', 'done'].includes(String(t.status)));

/** What the pill says: the live run first, then the task's own state. */
function taskStatus(t: Task, needsYou: boolean): string {
  if (needsYou) return 'waiting';
  if (isRunning(t)) return 'running';
  if (t.status === 'failed' || t.run_status === 'failed') return 'failed';
  if (isDone(t)) return isScheduled(t) ? 'scheduled' : 'completed';
  if (isScheduled(t)) return 'scheduled';
  return t.status || 'pending';
}

/** Every piece of work agents do on their own: one-off, scheduled, or woken by an event. */
export default function Tasks() {
  const { data, loading, error, refetch } = useResource<Task[]>('/tasks');
  const agents = useResource<AgentRow[]>('/agents');
  const watched = useResource<PullRequestWatch[]>('/github/watches');
  const notifications = useOptionalNotifications();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const show: Show = SHOWS.includes(params.get('show') as Show) ? (params.get('show') as Show) : 'all';

  usePolling(() => {
    refetch({ silent: true });
    watched.refetch({ silent: true });
  }, 15000, true);

  const agentById = useMemo(() => new Map((agents.data ?? []).map((a) => [a.id, a])), [agents.data]);
  const waitingIds = useMemo(() => {
    const ids = new Set<string>();
    for (const item of notifications?.inbox ?? []) {
      const taskId = (item as { task_id?: string | null }).task_id;
      if (taskId) ids.add(taskId);
    }
    return ids;
  }, [notifications?.inbox]);

  const tasks = useMemo(() => (data ?? []).filter((t) => !t.standing_agent_id), [data]);
  const needsYou = (t: Task) => waitingIds.has(t.id) || ['waiting', 'blocked', 'failed'].includes(String(t.status));
  const counts = {
    running: tasks.filter(isRunning).length,
    scheduled: tasks.filter(isScheduled).length,
    needs: tasks.filter(needsYou).length,
  };
  const watches = watched.data ?? [];
  const needle = query.trim().toLowerCase();
  const shown = tasks.filter((t) => {
    if (show === 'running' && !isRunning(t)) return false;
    if (show === 'scheduled' && !isScheduled(t)) return false;
    if (show === 'needs' && !needsYou(t)) return false;
    if (show === 'done' && !isDone(t)) return false;
    if (!needle) return true;
    return `${t.title} ${t.description ?? ''}`.toLowerCase().includes(needle);
  });

  const setShow = (next: Show) => {
    const copy = new URLSearchParams(params);
    if (next === 'all') copy.delete('show');
    else copy.set('show', next);
    setParams(copy, { replace: true });
  };

  const emptyWords = {
    all: { label: tr('ui.tasks.noTasksMatchYourSearch') },
    running: { label: tr('ui.tasks.nothingIsRunningRightNow') },
    scheduled: { label: tr('ui.tasks.nothingIsScheduledOpenA') },
    needs: { label: tr('ui.tasks.nothingNeedsYou') },
    done: { label: tr('ui.tasks.nothingHasFinishedYet') },
  };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.tasks.tasks')} subtitle={tr('ui.tasks.workYourAgentsDoOn')}>
        <NewButton to="/tasks/new" label={tr('ui.tasks.newTask_17b2')} />
      </PageHeader>

      {tasks.length > 0 && (
        <ListToolbar>
          <FilterChips<Show>
            label={tr('ui.tasks.show')}
            value={show}
            onChange={setShow}
            options={[
              { id: 'all', label: tr('ui.tasks.all') },
              { id: 'running', label: tr('ui.tasks.running'), count: counts.running },
              { id: 'scheduled', label: tr('ui.tasks.scheduled'), count: counts.scheduled },
              { id: 'needs', label: tr('ui.tasks.needsYou'), count: counts.needs },
              { id: 'done', label: tr('ui.tasks.done') },
            ]}
          />
          <ListSearch value={query} onChange={setQuery} placeholder={tr('ui.tasks.searchTasks')} />
        </ListToolbar>
      )}

      <div className="flex-1 overflow-y-auto">
        {(show === 'all' || show === 'scheduled') && watches.length > 0 && (
          <WatchedPullRequests watches={watches} onChange={() => watched.refetch({ silent: true })} />
        )}
        {loading && !data ? (
          <LoadingState label={tr('ui.tasks.loadingTasks_a349')} />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : tasks.length === 0 ? (
          watches.length === 0 && (
            <EmptyState
              icon={<TaskIcon />}
              title={tr('ui.tasks.noTasksYet')}
              desc={tr('ui.tasks.handAnAgentAPiece')}
              action={
                <Button asChild>
                  <Link to="/tasks/new">{tr('ui.tasks.createYourFirstTask')}</Link>
                </Button>
              }
            />
          )
        ) : shown.length === 0 ? (
          <p className="px-6 py-10 text-center text-[13.5px] text-fg-dim">{emptyWords[needle ? 'all' : show].label}</p>
        ) : (
          <ul>
            {shown.map((t) => {
              const agent = agentById.get(t.assigned_agents?.[0] ?? '');
              const schedule = scheduleInWords(t);
              const paused = Boolean(schedule) && !t.schedule_enabled;
              const next = t.schedule_enabled && t.next_run_at ? t.next_run_at : t.wake_pending ? t.wake_at : null;
              const others = (t.assigned_agents?.length ?? 0) - 1;
              return (
                <li key={t.id}>
                  <Link
                    to={`/tasks/${t.id}`}
                    className="group flex items-center gap-3 border-b border-line px-4 py-3 transition-colors hover:bg-panel-2 md:px-6"
                  >
                    <AgentBadge name={agent?.name ?? t.title} avatar={agent?.avatar} size="lg" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium transition-colors group-hover:text-accent-ink">{t.title}</div>
                      <div className="truncate text-[13px] text-fg-dim">
                        {[
                          agent ? (others > 0 ? `${agent.name} +${others}` : agent.name) : null,
                          schedule ? (paused ? <PausedSchedule key="s" schedule={schedule} /> : schedule) : null,
                          next ? <NextRun key="n" at={next} /> : null,
                          !schedule && t.run_completed_at ? <LastRun key="l" at={t.run_completed_at} /> : null,
                        ].filter(Boolean).map((part, index) => (
                          <span key={index}>{index > 0 && ' · '}{part}</span>
                        ))}
                      </div>
                    </div>
                    <StatusPill status={taskStatus(t, waitingIds.has(t.id))} />
                    <Chevron />
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

function PausedSchedule({ schedule }: { schedule: string }) {
  return <>{tr('ui.tasks.schedulePaused', { schedule })}</>;
}

function NextRun({ at }: { at: string }) {
  return <span title={friendlyTime(at)}>{tr('ui.tasks.nextRunRelativetime', { relativeTime: relativeTime(at) })}</span>;
}

function LastRun({ at }: { at: string }) {
  return <span title={friendlyTime(at)}>{tr('ui.tasks.lastRunRelativetime', { relativeTime: relativeTime(at) })}</span>;
}

function TaskIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 5h11M9 12h11M9 19h11" /><path d="M4 5l1.4 1.4L8 4M4 12l1.4 1.4L8 11M4 19l1.4 1.4L8 18" />
    </svg>
  );
}
