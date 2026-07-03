import { Link } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { PageHeader, LoadingState, ErrorState, EmptyState, Chevron } from '@/components/list-ui';

interface Team {
  id: string;
  name: string;
  description?: string;
  assigned_agents?: string[];
}

export default function Teams() {
  const { data, loading, error, refetch } = useResource<Team[]>('/teams');
  const teams = data ?? [];

  return (
    <div className="flex-1 flex flex-col h-screen min-w-0 overflow-hidden bg-bg text-fg">
      <PageHeader title="Teams" subtitle={`coordinate multiple agents · ${teams.length}`}>
        <Button asChild size="sm">
          <Link to="/teams/new">+ New team</Link>
        </Button>
      </PageHeader>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <LoadingState label="loading teams…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : teams.length === 0 ? (
          <EmptyState
            icon={<TeamIcon />}
            title="No teams yet"
            desc="Create a team so multiple agents can collaborate — assign roles and a leader."
            action={
              <Button asChild>
                <Link to="/teams/new">Create your first team</Link>
              </Button>
            }
          />
        ) : (
          <ul>
            {teams.map((t) => (
              <li key={t.id}>
                <Link
                  to={`/teams/${t.id}`}
                  className="group grid grid-cols-[1fr_auto] items-center gap-4 border-b border-line px-6 py-3.5 transition-colors hover:bg-panel-2"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="grid h-8 w-8 flex-none place-items-center rounded-sm border border-line bg-panel-2 text-fg-dim">
                      <TeamIcon />
                    </span>
                    <div className="min-w-0">
                      <div className="truncate font-medium transition-colors group-hover:text-accent-ink">{t.name}</div>
                      {t.description ? (
                        <div className="truncate text-[13px] text-fg-dim">{t.description}</div>
                      ) : (
                        <div className="font-mono text-[11px] text-fg-dim">{t.assigned_agents?.length || 0} agents · {t.id.slice(0, 8)}</div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-4 font-mono text-[11px] text-fg-dim">
                    <span className="tnum">{t.assigned_agents?.length || 0} agents</span>
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

function TeamIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="9" cy="8" r="3" /><path d="M3 20c0-3 2.5-5 6-5s6 2 6 5" /><path d="M16 6a3 3 0 0 1 0 6M21 20c0-2.5-1.5-4-3.5-4.5" />
    </svg>
  );
}
