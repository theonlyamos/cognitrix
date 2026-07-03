import { Link } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { PageHeader, LoadingState, ErrorState, EmptyState, Chevron } from '@/components/list-ui';
import { cn } from '@/lib/utils';

interface Task {
  id: string;
  title: string;
  description?: string;
  status?: string;
  done?: boolean;
}

const STATUS: Record<string, string> = {
  completed: 'text-ok border-ok/40',
  in_progress: 'text-accent-ink border-accent/40',
  failed: 'text-danger-ink border-danger/40',
  pending: 'text-fg-dim border-line',
};

export default function Tasks() {
  const { data, loading, error, refetch } = useResource<Task[]>('/tasks');
  const tasks = data ?? [];

  return (
    <div className="flex-1 flex flex-col h-screen min-w-0 overflow-hidden bg-bg text-fg">
      <PageHeader title="Tasks" subtitle={`delegate work to agents · ${tasks.length}`}>
        <Button asChild size="sm">
          <Link to="/tasks/new">+ New task</Link>
        </Button>
      </PageHeader>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <LoadingState label="loading tasks…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : tasks.length === 0 ? (
          <EmptyState
            icon={<TaskIcon />}
            title="No tasks yet"
            desc="Create a task to delegate work — it runs autonomously and reports back when complete."
            action={
              <Button asChild>
                <Link to="/tasks/new">Create your first task</Link>
              </Button>
            }
          />
        ) : (
          <ul>
            {tasks.map((t) => (
              <li key={t.id}>
                <Link
                  to={`/tasks/${t.id}`}
                  className="group grid grid-cols-[1fr_auto] items-center gap-4 border-b border-line px-6 py-3.5 transition-colors hover:bg-panel-2"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span
                      className={cn(
                        'grid h-4 w-4 flex-none place-items-center rounded-full border',
                        t.done ? 'border-ok text-ok' : 'border-line text-transparent',
                      )}
                    >
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round"><path d="M5 13l4 4L19 7" /></svg>
                    </span>
                    <div className="min-w-0">
                      <div className="truncate font-medium transition-colors group-hover:text-accent-ink">{t.title}</div>
                      {t.description && <div className="truncate text-[13px] text-fg-dim">{t.description}</div>}
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    <span className={cn('rounded border px-2 py-0.5 font-mono text-[10.5px]', STATUS[t.status || 'pending'] || STATUS.pending)}>
                      {t.status || 'pending'}
                    </span>
                    <Chevron />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function TaskIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 5h11M9 12h11M9 19h11" /><path d="M4 5l1.4 1.4L8 4M4 12l1.4 1.4L8 11M4 19l1.4 1.4L8 18" />
    </svg>
  );
}
