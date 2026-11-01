import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { api, errorMessage } from '@/lib/api';
import { ErrorState, LoadingState } from '@/components/list-ui';
import { MobileHeaderActions, type MobileHeaderAction } from '@/components/mobile-header-actions';
import { PROVIDER_LABELS } from '@/lib/providers';
import { cn } from '@/lib/utils';
import {
  TABS,
  draftFrom,
  draftProblem,
  handleFor,
  initials,
  payloadFrom,
  type AgentDraft,
  type AgentOptions,
  type AgentRecord,
  type Overview,
  type TabId,
  type TabProps,
  type ToolInfo,
} from '@/lib/agentDraft';
import { buttonClass, primaryButtonClass } from '@/components/agent/ui';
import OverviewTab from '@/components/agent/OverviewTab';
import GeneralTab from '@/components/agent/GeneralTab';
import ToolsTab from '@/components/agent/ToolsTab';
import FoldersTab from '@/components/agent/FoldersTab';
import ConnectionsTab from '@/components/agent/ConnectionsTab';
import TasksTab from '@/components/agent/TasksTab';
import TeamsTab from '@/components/agent/TeamsTab';

const TAB_VIEWS: Record<TabId, (props: TabProps) => JSX.Element> = {
  overview: OverviewTab,
  general: GeneralTab,
  tools: ToolsTab,
  folders: FoldersTab,
  connections: ConnectionsTab,
  tasks: TasksTab,
  teams: TeamsTab,
};

const isTab = (value: string | undefined): value is TabId => TABS.some((t) => t.id === value);

export default function AgentDetail() {
  const { agentId, tab: tabParam } = useParams();
  const navigate = useNavigate();
  const { data: agent, loading, error: loadError, refetch, setData: setAgent } = useResource<AgentRecord>(
    agentId ? `/agents/${agentId}` : null,
  );
  const { data: catalog } = useResource<ToolInfo[]>('/tools');
  const { data: options } = useResource<AgentOptions>('/agents/options');
  const {
    data: overview, error: overviewError, refetch: refetchOverview,
  } = useResource<Overview>(agentId ? `/agents/${agentId}/overview` : null);

  const [draft, setDraft] = useState<AgentDraft | null>(null);
  const [saved, setSaved] = useState<AgentDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [notice, setNotice] = useState('');
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  useEffect(() => {
    if (!agent) return;
    const fresh = draftFrom(agent);
    setDraft(fresh);
    setSaved(fresh);
  }, [agent]);

  const dirty = useMemo(() => Boolean(draft && saved && JSON.stringify(draft) !== JSON.stringify(saved)), [draft, saved]);
  const update = useCallback((patch: Partial<AgentDraft>) => {
    setDraft((cur) => (cur ? { ...cur, ...patch } : cur));
    setNotice('');
  }, []);

  // Closing or reloading the window with unsaved changes asks first. Tabs
  // share one draft, so switching tabs keeps the changes.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  // On narrow screens the tab row scrolls; keep the open tab in view.
  const ready = draft !== null;
  useEffect(() => {
    if (ready) tabRefs.current[tabParam || 'overview']?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [tabParam, ready]);

  if (tabParam === 'edit') return <Navigate to={`/agents/${agentId}/general`} replace />;
  const tab: TabId = isTab(tabParam) ? tabParam : 'overview';
  const goTo = (next: TabId) => navigate(next === 'overview' ? `/agents/${agentId}` : `/agents/${agentId}/${next}`);

  const save = async () => {
    if (!agent || !draft) return;
    const problem = draftProblem(draft);
    if (problem) {
      setSaveError(problem);
      return;
    }
    setSaving(true);
    setSaveError('');
    try {
      const res = await api.post<AgentRecord>('/agents', payloadFrom(agent.id, draft, catalog || [], agent.tools || []));
      setAgent(res.data);
      setNotice('Saved. New chats and task runs use these settings.');
      refetchOverview({ silent: true });
    } catch (e) {
      setSaveError(errorMessage(e, 'Could not save the agent.'));
    } finally {
      setSaving(false);
    }
  };

  const discard = () => {
    setDraft(saved);
    setSaveError('');
  };

  const remove = async () => {
    if (!agent || !window.confirm('Delete this agent? This cannot be undone.')) return;
    try {
      setSaved(draft);
      await api.delete(`/agents/${agent.id}`);
      navigate('/agents');
    } catch (e) {
      setSaveError(errorMessage(e, 'Could not delete the agent.'));
    }
  };

  const openChat = (sessionId = '') => {
    if (!agent) return;
    localStorage.setItem('selectedAgentId', agent.id);
    localStorage.setItem(`chatSession:${agent.id}`, sessionId);
    navigate('/home');
  };

  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!step && e.key !== 'Home' && e.key !== 'End') return;
    e.preventDefault();
    const nextIndex = e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : (index + step + TABS.length) % TABS.length;
    const next = TABS[nextIndex].id;
    goTo(next);
    tabRefs.current[next]?.focus();
  };

  if (loading && !agent) {
    return <div className="flex h-full min-w-0 flex-1 flex-col bg-bg"><LoadingState label="loading agent…" /></div>;
  }
  if (loadError || !agent || !draft) {
    return (
      <div className="flex h-full min-w-0 flex-1 flex-col bg-bg">
        <ErrorState message={loadError || 'Agent not found.'} onRetry={refetch} />
      </div>
    );
  }

  const mobileActions: MobileHeaderAction[] = [
    { key: 'task', label: 'New task', to: `/tasks/new?agentId=${encodeURIComponent(agent.id)}` },
    { key: 'delete', label: 'Delete agent', destructive: true, onSelect: remove },
  ];
  const running = overview?.stats.running_chats || 0;
  const View = TAB_VIEWS[tab];
  const props: TabProps = {
    agent, draft, update, overview: overview || null, overviewError,
    refreshOverview: () => refetchOverview({ silent: true }), options: options || null, catalog: catalog || [], goTo,
    openChat, remove,
  };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <header className="app-page-header flex min-h-14 flex-none flex-row flex-nowrap items-center gap-2 border-b border-line py-2 pl-16 pr-4 md:px-8">
        <nav aria-label="Breadcrumb" className="flex min-w-0 flex-1 items-center gap-1.5 text-[13px] text-fg-dim">
          <Link to="/agents" className="hover:text-fg hover:underline">Agents</Link>
          <span aria-hidden>›</span>
          <span className="truncate text-fg" aria-current="page">{agent.name}</span>
        </nav>
        <div className="flex flex-none items-center gap-2">
          <button type="button" onClick={() => openChat()} className={cn(primaryButtonClass, 'px-3 md:px-4')}>
            Chat<span className="hidden sm:inline">&nbsp;with agent</span>
          </button>
          <Link to={`/tasks/new?agentId=${encodeURIComponent(agent.id)}`} className={cn(buttonClass, 'hidden md:inline-flex')}>New task</Link>
          <MobileHeaderActions id={`agent-${agent.id}-actions`} actions={mobileActions} />
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[928px] px-4 pb-28 pt-5 md:px-8 md:pt-7">
          <div className="flex items-center gap-3.5">
            <div aria-hidden className="grid h-12 w-12 flex-none place-items-center rounded-[10px] border border-accent bg-accent/10 text-[17px] font-bold text-accent-ink">
              {initials(draft.name || agent.name)}
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-[22px] font-bold tracking-tight md:text-[24px]">{agent.name}</h1>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[13px] text-fg-dim">
                <span className="font-mono text-[12px]">{handleFor(agent.name)}</span>
                <span>
                  {PROVIDER_LABELS[agent.llm?.provider || ''] || agent.llm?.provider || '—'} · <span className="font-mono text-[12px]">{agent.llm?.model || '—'}</span>
                </span>
                {running > 0 && (
                  <span className="inline-flex items-center gap-1.5 text-ok">
                    <span aria-hidden className="h-[7px] w-[7px] rounded-full bg-ok" />
                    {running} {running === 1 ? 'chat' : 'chats'} running
                  </span>
                )}
              </div>
              {agent.description && <p className="mt-1 text-[13.5px] text-fg-dim">{agent.description}</p>}
            </div>
          </div>

          <div role="tablist" aria-label="Agent sections" className="-mx-4 mt-5 flex gap-1 overflow-x-auto border-b border-line px-4 md:mx-0 md:px-0">
            {TABS.map((t, i) => (
              <button
                key={t.id}
                ref={(el) => { tabRefs.current[t.id] = el; }}
                type="button"
                role="tab"
                id={`agent-tab-${t.id}`}
                aria-selected={t.id === tab}
                aria-controls="agent-tab-panel"
                tabIndex={t.id === tab ? 0 : -1}
                onClick={() => goTo(t.id)}
                onKeyDown={(e) => onTabKey(e, i)}
                className={cn(
                  '-mb-px flex-none whitespace-nowrap border-b-2 px-3 py-2.5 text-[14px] transition-colors',
                  t.id === tab ? 'border-accent font-medium text-fg' : 'border-transparent text-fg-dim hover:text-fg',
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div id="agent-tab-panel" role="tabpanel" aria-labelledby={`agent-tab-${tab}`} className="mt-5 md:mt-6">
            <View {...props} />
          </div>
        </div>
      </div>

      {(dirty || saveError || notice) && (
        <div className="flex-none border-t border-line bg-panel px-4 py-3 md:px-8">
          <div className="mx-auto flex w-full max-w-[864px] flex-wrap items-center gap-2.5">
            <p role="status" className={cn('min-w-[200px] flex-1 text-[13px]', saveError ? 'text-danger-ink' : 'text-fg-dim')}>
              {saveError || (dirty ? 'You have unsaved changes. Chats already running keep their old settings.' : notice)}
            </p>
            {dirty && (
              <>
                <button type="button" onClick={discard} disabled={saving} className={buttonClass}>Discard</button>
                <button type="button" onClick={() => void save()} disabled={saving} className={primaryButtonClass}>
                  {saving ? 'Saving…' : 'Save changes'}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
