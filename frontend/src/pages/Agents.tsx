import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { agentFromFile } from '@/lib/agentImport';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { PageHeader, LoadingState, ErrorState, EmptyState, Chevron, NewButton, FilterChips, ListSearch, ListToolbar } from '@/components/list-ui';
import { AgentBadge } from '@/components/StartChat';
import type { AgentAvatar } from '@/lib/agentAvatar';
import { modelName } from '@/lib/plain';
import Teams from '@/pages/Teams';
import type { OnboardingStatus, StarterAgent } from '@/components/AssistantSetup';
import { tr } from '@/lib/i18n';

interface Agent {
  id: string;
  name: string;
  description?: string;
  avatar?: AgentAvatar | null;
  llm?: { provider?: string; model?: string };
  tools?: unknown[];
  my_role?: 'owner' | 'edit' | 'use';
  shared?: boolean;
  owner_id?: string | null;
}

export default function Agents() {
  const { data, loading, error, refetch } = useResource<Agent[]>('/agents');
  // Drop phantom null-id rows (leftover default "Assistant" agents).
  const agents = (data ?? []).filter((a) => a.id);
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab: 'agents' | 'teams' = params.get('tab') === 'teams' ? 'teams' : 'agents';
  const [query, setQuery] = useState('');
  const needle = query.trim().toLowerCase();
  const shown = needle ? agents.filter((a) => `${a.name} ${a.description ?? ''}`.toLowerCase().includes(needle)) : agents;
  const fileInput = useRef<HTMLInputElement>(null);
  const [importError, setImportError] = useState('');
  // Specialists from the starter team this person doesn't have yet.
  const [missingStarters, setMissingStarters] = useState<StarterAgent[]>([]);
  const [addingStarters, setAddingStarters] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<OnboardingStatus>('/onboarding')
      .then((r) => {
        if (!cancelled) setMissingStarters((r.data?.starter_team ?? []).filter((a) => a.missing));
      })
      // An older engine, or an API key session: no starter team to offer.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const addStarterTeam = async () => {
    setImportError('');
    setAddingStarters(true);
    try {
      await api.post('/onboarding/starter-team');
      setMissingStarters([]);
      refetch();
    } catch (e) {
      setImportError(errorMessage(e, tr('ui.agents.couldNotAddTheStarter')));
    } finally {
      setAddingStarters(false);
    }
  };

  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setImportError('');
    try {
      const res = await api.post<{ id: string }>('/agents', agentFromFile(await file.text()));
      if (res.data?.id) navigate(`/agents/${res.data.id}`);
      else refetch();
    } catch (e) {
      setImportError(errorMessage(e, e instanceof Error ? e.message : tr('ui.agents.couldNotImportTheAgent')));
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full min-w-0 overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.agents.agents')} subtitle={tr('ui.agents.theAssistantsThatWorkFor')}>
        <input ref={fileInput} type="file" accept=".json,application/json" className="hidden" aria-label={tr('ui.agents.agentFile')} onChange={importFile} />
        {tab === 'agents' && <Button type="button" size="sm" variant="ghost" className="max-sm:hidden" onClick={() => fileInput.current?.click()}>{tr('ui.agents.import')}</Button>}
        {tab === 'agents' ? <NewButton to="/agents/new" label={tr('ui.agents.newAgent_d200')} /> : <NewButton to="/teams/new" label={tr('ui.agents.newTeam')} />}
      </PageHeader>
      <ListToolbar>
        <FilterChips<'agents' | 'teams'>
          label={tr('ui.agents.show')}
          value={tab}
          onChange={(next) => setParams(next === 'teams' ? { tab: 'teams' } : {}, { replace: true })}
          options={[{ id: 'agents', label: tr('ui.agents.agents') }, { id: 'teams', label: tr('ui.agents.teams') }]}
        />
        {tab === 'agents' && agents.length > 6 && <ListSearch value={query} onChange={setQuery} placeholder={tr('ui.agents.searchAgents')} />}
      </ListToolbar>
      {importError && (
        <p role="alert" className="border-b border-line px-6 py-2 text-[12.5px] text-danger-ink">{importError}</p>
      )}
      {tab === 'agents' && missingStarters.length > 0 && agents.length > 0 && (
        <div className="flex flex-col gap-2 border-b border-line bg-panel-2/60 px-6 py-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="min-w-0 text-[13px] leading-relaxed text-fg-dim">
            <span className="font-medium text-fg">{tr('ui.agents.starterTeam')}</span>{' '}{tr('ui.agents.addMapSpecialistsYourAssistant', { map: missingStarters.map((a) => a.name).join(', ') })}
          </p>
          <Button type="button" size="sm" variant="outline" className="shrink-0" onClick={() => void addStarterTeam()} disabled={addingStarters}>
            {addingStarters ? tr('ui.agents.adding') : tr('ui.agents.addStarterTeam')}
          </Button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {tab === 'teams' ? (
          <Teams />
        ) : loading ? (
          <LoadingState label={tr('ui.agents.loadingAgents')} />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : agents.length === 0 ? (
          <EmptyState
            icon={<AgentIcon />}
            title={tr('ui.agents.noAgentsYet')}
            desc={tr('ui.agents.createAnAgentPickA')}
            action={
              <Button asChild>
                <Link to="/agents/new">{tr('ui.agents.createYourFirstAgent')}</Link>
              </Button>
            }
          />
        ) : (
          <ul>
            {shown.map((a) => (
              <li key={a.id}>
                <Link
                  to={`/agents/${a.id}`}
                  className="group flex items-center gap-3 border-b border-line px-4 py-3 transition-colors hover:bg-panel-2 md:px-6"
                >
                  <AgentBadge name={a.name} avatar={a.avatar} size="lg" />
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate font-medium transition-colors group-hover:text-accent-ink">{a.name}</span>
                      {a.owner_id && a.my_role && a.my_role !== 'owner' ? (
                        <span className="flex-none rounded-full border border-line px-2 py-px text-[11px] text-fg-dim">{tr('ui.agents.sharedWithYou')}</span>
                      ) : a.shared ? (
                        <span className="flex-none rounded-full border border-line px-2 py-px text-[11px] text-fg-dim">{tr('ui.agents.shared')}</span>
                      ) : null}
                    </div>
                    {a.description && <div className="truncate text-[13px] text-fg-dim">{a.description}</div>}
                  </div>
                  <span className="hidden flex-none text-[12.5px] text-fg-dim sm:inline">{modelName(a.llm?.model)}</span>
                  <Chevron />
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
