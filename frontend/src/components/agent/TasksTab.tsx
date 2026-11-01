import { useId } from 'react';
import { Link } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { TaskAssignmentPanel, type TaskRecord } from '@/components/TaskAssignmentPanel';
import { scheduleText, timeAgo, type TabProps } from '@/lib/agentDraft';
import { Card, Check, Pill, buttonClass, inputClass, linkButtonClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';

interface AutomationRow {
  id: string;
  title: string;
  triggers: { id: string; name: string; enabled: boolean; fire_count: number; last_fired_at: string | null }[];
}

const STATUS_TONE: Record<string, 'ok' | 'danger' | 'accent' | 'dim'> = {
  completed: 'ok',
  failed: 'danger',
  running: 'accent',
  queued: 'accent',
};

export default function TasksTab({ agent, draft, update, overview, refreshOverview, options }: TabProps) {
  const capId = useId();
  const { data: allTasks, error: tasksError, refetch: refetchTasks } = useResource<TaskRecord[]>('/tasks');
  const { data: automations } = useResource<AutomationRow[]>('/automations');

  const tasks = overview?.tasks || [];
  const taskIds = new Set(tasks.map((t) => t.id));
  const hooks = (automations || [])
    .filter((a) => taskIds.has(a.id))
    .flatMap((a) => a.triggers.map((t) => ({ ...t, taskTitle: a.title })));

  return (
    <div className="flex flex-col gap-[18px]">
      <Card
        title="Assigned tasks"
        aside={(
          <>
            {tasksError ? (
              <span role="alert" className="flex items-center gap-2 text-[12.5px] text-danger-ink">
                {tasksError}
                <button type="button" onClick={() => void refetchTasks()} className={linkButtonClass}>Retry tasks</button>
              </span>
            ) : (
              <TaskAssignmentPanel mode="agent" entityId={agent.id} tasks={allTasks || []}
                onAssigned={() => { refreshOverview(); refetchTasks({ silent: true }); }} />
            )}
            <Link to={`/tasks/new?agentId=${encodeURIComponent(agent.id)}`} className={buttonClass}>+ New task</Link>
          </>
        )}
      >
        <div className="overflow-hidden rounded-lg border border-line">
          <div className="hidden grid-cols-[minmax(0,1fr)_150px_140px_110px] gap-3 bg-panel-2 px-3.5 py-2 text-[12px] text-fg-dim md:grid">
            <span>Task</span><span>Runs</span><span>Last run</span><span>Status</span>
          </div>
          {!overview ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim md:border-t md:border-line">Loading…</p>
          ) : tasks.length === 0 ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim md:border-t md:border-line">No tasks assigned to this agent yet.</p>
          ) : tasks.map((t) => (
            <Link key={t.id} to={`/tasks/${t.id}`}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-t border-line px-3.5 py-3 first:border-t-0 hover:bg-panel-2 md:grid-cols-[minmax(0,1fr)_150px_140px_110px] md:first:border-t">
              <span className="truncate text-[14.5px] font-medium">{t.title || 'Untitled task'}</span>
              <span className="col-start-1 row-start-2 text-[12.5px] text-fg-dim md:col-start-auto md:row-start-auto">
                {scheduleText(t)}{t.wake_at ? ' · checks back' : ''}
              </span>
              <span className="hidden text-[12.5px] text-fg-dim md:block">
                {t.last_run_at ? `${timeAgo(t.last_run_at)}${t.last_run_status ? ` · ${t.last_run_status}` : ''}` : 'never'}
              </span>
              <span className="col-start-2 row-span-2 row-start-1 md:col-start-auto md:row-span-1 md:row-start-auto">
                <Pill tone={STATUS_TONE[t.status] || 'dim'}>{t.status || 'pending'}</Pill>
              </span>
            </Link>
          ))}
        </div>
      </Card>

      <Card title="Always-on" description="How this agent behaves when it runs on its own.">
        <div className="flex flex-col gap-4">
          <Check
            checked={draft.selfCheckins}
            onChange={(selfCheckins) => update({ selfCheckins })}
            label="Let it schedule its own check-ins"
            detail="During a task it can decide to come back later, for example to see if a build finished. Off removes the Schedule Next Check tool."
          />
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor={capId} className="min-w-[220px] flex-1">
              <span className="block text-[14.5px] font-medium">Most runs per day</span>
              <span className="mt-0.5 block text-[13px] text-fg-dim">Per task. A task can set its own limit, which wins.</span>
            </label>
            <input id={capId} inputMode="numeric" value={draft.maxRunsPerDay}
              onChange={(e) => update({ maxRunsPerDay: e.target.value.replace(/[^\d]/g, '') })}
              placeholder={options ? `${options.default_max_runs_per_day} (default)` : 'default'}
              className={cn(inputClass, 'w-[160px]')} />
          </div>
        </div>
      </Card>

      <Card title="Webhooks" aside={<Link to="/automations" className={linkButtonClass}>Open Automations</Link>}>
        {automations === null ? (
          <p className="text-[13px] text-fg-dim">Loading…</p>
        ) : hooks.length === 0 ? (
          <p className="text-[13px] text-fg-dim">None of this agent's tasks have a webhook. Add one from a task's page.</p>
        ) : (
          <ul className="-mt-1">
            {hooks.map((h) => (
              <li key={h.id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 border-t border-line py-2.5 text-[14px] first:border-t-0">
                <span className="min-w-[160px] flex-1 truncate font-mono text-[13px]">{h.name}</span>
                <span className="text-[12.5px] text-fg-dim">for “{h.taskTitle}”</span>
                <span className="text-[12.5px] text-fg-dim">
                  {h.enabled ? '' : 'off · '}{h.fire_count} {h.fire_count === 1 ? 'call' : 'calls'}{h.last_fired_at ? ` · last ${timeAgo(h.last_fired_at)}` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
