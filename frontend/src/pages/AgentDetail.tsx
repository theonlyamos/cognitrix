import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { ErrorState, LoadingState } from '@/components/list-ui';
import { TaskAssignmentPanel, type TaskRecord } from '@/components/TaskAssignmentPanel';

interface Tool {
  name: string;
  description?: string;
  category?: string;
}

interface AgentData {
  id: string;
  name: string;
  system_prompt?: string;
  llm?: {
    provider?: string;
    model?: string;
    temperature?: number;
    max_tokens?: number;
    base_url?: string;
  };
  tools?: Tool[];
}

export default function AgentDetail() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const { data: agent, loading, error: loadError, refetch } = useResource<AgentData>(
    agentId ? `/agents/${agentId}` : null,
  );
  const {
    data: taskList,
    loading: loadingTasks,
    error: taskError,
    refetch: refetchTasks,
  } = useResource<TaskRecord[]>('/tasks');
  const [actionError, setActionError] = useState('');

  const prepareChat = () => {
    if (!agent) return;
    localStorage.setItem('selectedAgentId', agent.id);
    localStorage.setItem(`chatSession:${agent.id}`, '');
  };

  const remove = async () => {
    if (!agent || !confirm('Delete this agent? This cannot be undone.')) return;
    setActionError('');
    try {
      await api.delete(`/agents/${agent.id}`);
      navigate('/agents');
    } catch (error) {
      setActionError(errorMessage(error, 'Could not delete the agent.'));
    }
  };

  if (loading) {
    return <div className="flex h-screen min-w-0 flex-1 flex-col bg-bg"><LoadingState label="loading agent…" /></div>;
  }
  if (loadError || !agent) {
    return (
      <div className="flex h-screen min-w-0 flex-1 flex-col bg-bg">
        <ErrorState message={loadError || 'Agent not found.'} onRetry={refetch} />
      </div>
    );
  }

  const tools = agent.tools || [];
  const tasks = taskList || [];
  const assignedTasks = tasks.filter((task) => (task.assigned_agents || []).includes(agent.id));
  const providerModel = `${agent.llm?.provider || '—'} · ${agent.llm?.model || '—'}`;

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <header className="app-page-header flex flex-none flex-col items-stretch gap-3 border-b border-line py-3 pl-16 pr-4 sm:flex-row sm:flex-wrap sm:items-center md:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Link to="/agents" aria-label="Back to agents" className="grid h-11 w-11 flex-none place-items-center rounded border border-line text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:h-8 md:w-8">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
          </Link>
          <div className="min-w-0">
            <p className="font-mono text-[10px] tracking-[0.18em] text-accent-ink">AGENT</p>
            <h1 className="truncate text-lg font-semibold tracking-tight">{agent.name}</h1>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
          <Button asChild size="sm"><Link to="/home" onClick={prepareChat}>Interact</Link></Button>
          <Button asChild variant="outline" size="sm"><Link to={`/agents/${agent.id}/edit`}>Edit</Link></Button>
          <Button variant="ghost" size="sm" onClick={remove} className="hover:text-danger-ink">Delete</Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-8 px-6 py-8">
          {actionError && <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{actionError}</p>}

          <section className="space-y-3">
            <div>
              <p className="font-mono text-[11px] tracking-[0.16em] text-fg-dim">MODEL</p>
              <p className="mt-1 text-sm font-medium">{providerModel}</p>
            </div>
            <div className="flex flex-wrap gap-x-6 gap-y-1 font-mono text-[11px] text-fg-dim">
              <span>temperature {agent.llm?.temperature ?? '—'}</span>
              <span>max tokens {agent.llm?.max_tokens ?? 'provider default'}</span>
              <span>agent:{agent.id.slice(0, 8)}</span>
            </div>
            {agent.llm?.base_url && <p className="break-all font-mono text-[11px] text-fg-dim">{agent.llm.base_url}</p>}
          </section>

          <section>
            <h2 className="mb-3 font-mono text-[11px] tracking-[0.16em] text-fg-dim">SYSTEM PROMPT</h2>
            <p className="whitespace-pre-wrap rounded border border-line bg-panel px-4 py-3 text-sm text-fg-dim">
              {agent.system_prompt || 'No system prompt configured.'}
            </p>
          </section>

          <section>
            <h2 className="mb-3 font-mono text-[11px] tracking-[0.16em] text-fg-dim">TOOLS · {tools.length}</h2>
            {tools.length === 0 ? (
              <p className="text-sm text-fg-dim">No tools configured.</p>
            ) : (
              <ul className="rounded border border-line">
                {tools.map((tool) => (
                  <li key={tool.name} className="border-b border-line px-4 py-3 last:border-b-0">
                    <div className="font-mono text-[12px] text-fg">{tool.name}</div>
                    {tool.description && <div className="mt-0.5 text-[12px] text-fg-dim">{tool.description}</div>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-mono text-[11px] tracking-[0.16em] text-fg-dim">TASKS · {taskList ? assignedTasks.length : '—'}</h2>
              <div className="flex flex-wrap items-center gap-2">
                {!loadingTasks && !taskError && (
                  <TaskAssignmentPanel mode="agent" entityId={agent.id} tasks={tasks} onAssigned={() => refetchTasks({ silent: true })} />
                )}
                <Button asChild variant="ghost" size="sm"><Link to={`/tasks/new?agentId=${encodeURIComponent(agent.id)}`}>New task</Link></Button>
              </div>
            </div>
            {loadingTasks ? (
              <p role="status" className="mt-3 text-sm text-fg-dim">Loading tasks…</p>
            ) : taskError ? (
              <div role="alert" className="mt-3 flex flex-wrap items-center justify-between gap-3 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[11px] text-danger-ink">
                <span>{taskError}</span>
                <Button variant="ghost" size="sm" onClick={() => refetchTasks()}>Retry tasks</Button>
              </div>
            ) : assignedTasks.length === 0 ? (
              <p className="mt-3 text-sm text-fg-dim">No tasks assigned to this agent.</p>
            ) : (
              <ul className="mt-3 rounded border border-line">
                {assignedTasks.map((task) => (
                  <li key={task.id}>
                    <Link aria-label={task.title} aria-describedby={`agent-task-status-${task.id}`} to={`/tasks/${task.id}`} className="group flex items-center gap-3 border-b border-line px-4 py-3 last:border-b-0 hover:bg-panel-2">
                      <div className="min-w-0 flex-1 truncate text-[14px] font-medium group-hover:text-accent-ink">{task.title}</div>
                      <span id={`agent-task-status-${task.id}`} className="rounded border border-line px-2 py-0.5 font-mono text-[10.5px] text-fg-dim">{task.status || 'pending'}</span>
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
