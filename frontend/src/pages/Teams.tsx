import { Link } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { PageHeader, LoadingState, ErrorState, EmptyState, Chevron } from '@/components/list-ui';
import { tr, trn } from '@/lib/i18n';

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
    <div className="flex-1 flex flex-col h-full min-w-0 overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.teams.teams')} subtitle={trn(teams.length, 'ui.teams.countPrivate_one', 'ui.teams.countPrivate_other')}>
        <Button asChild size="sm">
          <Link to="/teams/new">{tr('ui.teams.newTeam')}</Link>
        </Button>
      </PageHeader>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <LoadingState label={tr('ui.teams.loadingTeams')} />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : teams.length === 0 ? (
          <EmptyState
            icon={<TeamIcon />}
            title={tr('ui.teams.noTeamsYet')}
            desc={tr('ui.teams.createATeamSoMultiple')}
            action={
              <Button asChild>
                <Link to="/teams/new">{tr('ui.teams.createYourFirstTeam')}</Link>
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
                        <div className="font-mono text-[11px] text-fg-dim">{tr('ui.teams.lengthAgentsSlice', { length: t.assigned_agents?.length || 0, slice: t.id.slice(0, 8) })}</div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-4 font-mono text-[11px] text-fg-dim">
                    <span className="tnum">{tr('ui.teams.lengthAgents', { length: t.assigned_agents?.length || 0 })}</span>
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
