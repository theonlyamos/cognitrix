import { Link } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { PageHeader, LoadingState, ErrorState, EmptyState, Chevron } from '@/components/list-ui';

interface Agent {
  id: string;
  name: string;
  llm?: { provider?: string; model?: string };
  tools?: unknown[];
}

export default function Agents() {
  const { data, loading, error, refetch } = useResource<Agent[]>('/agents');
  // Drop phantom null-id rows (leftover default "Assistant" agents).
  const agents = (data ?? []).filter((a) => a.id);

  return (
    <div className="flex-1 flex flex-col h-screen min-w-0 overflow-hidden bg-bg text-fg">
      <PageHeader title="Agents" subtitle={`configure and run agents · ${agents.length}`}>
        <Button asChild size="sm">
          <Link to="/agents/new">+ New agent</Link>
        </Button>
      </PageHeader>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <LoadingState label="loading agents…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : agents.length === 0 ? (
          <EmptyState
            icon={<AgentIcon />}
            title="No agents yet"
            desc="Create an agent — pick a provider and model, give it a system prompt and tools."
            action={
              <Button asChild>
                <Link to="/agents/new">Create your first agent</Link>
              </Button>
            }
          />
        ) : (
          <ul>
            {agents.map((a) => (
              <li key={a.id}>
                <Link
                  to={`/agents/${a.id}`}
                  className="group grid grid-cols-[1fr_auto] items-center gap-4 border-b border-line px-6 py-3.5 transition-colors hover:bg-panel-2"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="grid h-8 w-8 flex-none place-items-center rounded-sm border border-line bg-panel-2 text-fg-dim">
                      <AgentIcon />
                    </span>
                    <div className="min-w-0">
                      <div className="truncate font-medium transition-colors group-hover:text-accent-ink">{a.name}</div>
                      <div className="truncate font-mono text-[11px] text-fg-dim">
                        {a.llm?.provider || '—'} · {a.llm?.model || '—'} · <span className="opacity-70">{a.id.slice(0, 8)}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 font-mono text-[11px] text-fg-dim">
                    <span className="tnum">{a.tools?.length ?? 0} tools</span>
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

function AgentIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="13" rx="1.5" /><path d="M8 20h8M12 17v3" />
    </svg>
  );
}
