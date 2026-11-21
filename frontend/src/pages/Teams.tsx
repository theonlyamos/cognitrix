import { Link } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { LoadingState, ErrorState, EmptyState, Chevron } from '@/components/list-ui';
import { AgentBadge } from '@/components/StartChat';
import type { AgentAvatar } from '@/lib/agentAvatar';
import { tr, trn } from '@/lib/i18n';

interface Team {
  id: string;
  name: string;
  description?: string;
  assigned_agents?: string[];
}

/** The list of teams: a tab of the Agents page. */
export default function Teams() {
  const { data, loading, error, refetch } = useResource<Team[]>('/teams');
  const agents = useResource<{ id: string; name: string; avatar?: AgentAvatar | null }[]>('/agents');
  const teams = data ?? [];
  const byId = new Map((agents.data ?? []).map((a) => [a.id, a]));

  if (loading) return <LoadingState label={tr('ui.teams.loadingTeams')} />;
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (teams.length === 0) {
    return (
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
    );
  }
  return (
    <ul>
      {teams.map((t) => {
        const members = (t.assigned_agents ?? []).map((id) => byId.get(id)).filter(Boolean) as { id: string; name: string; avatar?: AgentAvatar | null }[];
        return (
          <li key={t.id}>
            <Link
              to={`/teams/${t.id}`}
              className="group flex items-center gap-3 border-b border-line px-4 py-3 transition-colors hover:bg-panel-2 md:px-6"
            >
              <AgentBadge name={t.name} team size="lg" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium transition-colors group-hover:text-accent-ink">{t.name}</div>
                <div className="truncate text-[13px] text-fg-dim">
                  {t.description || members.map((m) => m.name).join(', ') || trn(t.assigned_agents?.length || 0, 'ui.teams.members_one', 'ui.teams.members_other')}
                </div>
              </div>
              <span className="hidden flex-none items-center -space-x-1 sm:flex">
                {members.slice(0, 5).map((m) => <AgentBadge key={m.id} name={m.name} avatar={m.avatar} />)}
              </span>
              <span className="flex-none text-[12.5px] text-fg-dim">{trn(t.assigned_agents?.length || 0, 'ui.teams.members_one', 'ui.teams.members_other')}</span>
              <Chevron />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function TeamIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="9" cy="8" r="3" /><path d="M3 20c0-3 2.5-5 6-5s6 2 6 5" /><path d="M16 6a3 3 0 0 1 0 6M21 20c0-2.5-1.5-4-3.5-4.5" />
    </svg>
  );
}
