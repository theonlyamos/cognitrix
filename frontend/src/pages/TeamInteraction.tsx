import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { LoadingState, ErrorState, Chevron } from '@/components/list-ui';
import { cn } from '@/lib/utils';

interface Agent { id: string; name: string; llm?: { provider?: string; model?: string } }
interface Task { id: string; title: string; status?: string; team_id?: string | null }
interface Team {
  id: string;
  name: string;
  description?: string;
  assigned_agents?: string[];
  leader_id?: string | null;
}

export default function TeamInteraction() {
  const { teamId } = useParams();
  const { data: team, loading, error, refetch } = useResource<Team>(teamId ? `/teams/${teamId}` : null);
  const { data: agentList } = useResource<Agent[]>('/agents');
  const { data: taskList } = useResource<Task[]>('/tasks');

  const agentsById = useMemo(() => {
    const m = new Map<string, Agent>();
    for (const a of agentList || []) if (a.id) m.set(a.id, a);
    return m;
  }, [agentList]);

  const members = useMemo(
    () => (team?.assigned_agents || []).map((id) => agentsById.get(id)).filter(Boolean) as Agent[],
    [team, agentsById],
  );
  const tasks = useMemo(() => (taskList || []).filter((t) => t.team_id === teamId), [taskList, teamId]);

  if (loading) {
    return <div className="flex-1 flex flex-col h-screen min-w-0 bg-bg"><LoadingState label="loading team…" /></div>;
  }
  if (error || !team) {
    return (
      <div className="flex-1 flex flex-col h-screen min-w-0 bg-bg">
        <ErrorState message={error || 'Team not found.'} onRetry={refetch} />
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-screen min-w-0 overflow-hidden bg-bg text-fg">
      <header className="app-page-header flex flex-none flex-col items-stretch gap-3 border-b border-line py-3 pl-16 pr-4 sm:flex-row sm:flex-wrap sm:items-center md:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Link to="/teams" aria-label="Back to teams" className="grid h-11 w-11 flex-none place-items-center rounded border border-line text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:h-8 md:w-8">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
          </Link>
          <div className="min-w-0">
            <p className="font-mono text-[10px] tracking-[0.18em] text-accent-ink">TEAM</p>
            <h1 className="truncate text-lg font-semibold tracking-tight">{team.name}</h1>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
          <Button asChild variant="outline" size="sm"><Link to={`/teams/${team.id}`}>Edit</Link></Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-8 px-6 py-8">
          {team.description && <p className="max-w-2xl text-fg-dim">{team.description}</p>}
          <div className="font-mono text-[11px] text-fg-dim">team:{team.id.slice(0, 8)} · {members.length} members · {tasks.length} tasks</div>

          {/* Members */}
          <section>
            <h2 className="mb-3 font-mono text-[11px] tracking-[0.16em] text-fg-dim">MEMBERS</h2>
            {members.length === 0 ? (
              <p className="text-sm text-fg-dim">No agents assigned. <Link to={`/teams/${team.id}`} className="text-accent-ink hover:underline">Add members →</Link></p>
            ) : (
              <ul className="rounded border border-line">
                {members.map((a) => (
                  <li key={a.id}>
                    <Link to={`/agents/${a.id}`} className="group flex items-center gap-3 border-b border-line px-4 py-3 last:border-b-0 hover:bg-panel-2">
                      <span className="grid h-7 w-7 flex-none place-items-center rounded-sm border border-line bg-panel-2 text-[11px] font-bold text-accent-ink">{a.name[0]?.toUpperCase()}</span>
                      <div className="min-w-0">
                        <div className="truncate text-[14px] font-medium group-hover:text-accent-ink">
                          {a.name}
                          {team.leader_id === a.id && <span className="ml-2 rounded border border-accent/40 px-1.5 py-px font-mono text-[10px] text-accent-ink">LEAD</span>}
                        </div>
                        <div className="truncate font-mono text-[10.5px] text-fg-dim">{a.llm?.provider || '—'} · {a.llm?.model || '—'}</div>
                      </div>
                      <Chevron />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Tasks */}
          <section>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-mono text-[11px] tracking-[0.16em] text-fg-dim">TASKS</h2>
              <Button asChild variant="ghost" size="sm"><Link to="/tasks/new">+ New task</Link></Button>
            </div>
            {tasks.length === 0 ? (
              <p className="text-sm text-fg-dim">No tasks for this team yet.</p>
            ) : (
              <ul className="rounded border border-line">
                {tasks.map((t) => (
                  <li key={t.id}>
                    <Link to={`/tasks/${t.id}`} className="group flex items-center gap-3 border-b border-line px-4 py-3 last:border-b-0 hover:bg-panel-2">
                      <div className="min-w-0 flex-1 truncate text-[14px] font-medium group-hover:text-accent-ink">{t.title}</div>
                      <span className={cn('rounded border px-2 py-0.5 font-mono text-[10.5px]', t.status === 'completed' ? 'border-ok/40 text-ok' : t.status === 'in_progress' ? 'border-accent/40 text-accent-ink' : t.status === 'failed' ? 'border-danger/40 text-danger-ink' : t.status === 'cancelled' ? 'border-danger/30 text-fg-dim' : 'border-line text-fg-dim')}>{t.status || 'pending'}</span>
                      <Chevron />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
