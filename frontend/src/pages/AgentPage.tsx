import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { useSystemInfo } from '@/hooks/useSystemInfo';
import { Input } from '@/lib/components/ui/input';
import { Textarea } from '@/lib/components/ui/textarea';
import { Select } from '@/lib/components/ui/select';
import { LoadingState } from '@/components/list-ui';
import { Field, PageForm } from '@/components/form';
import { cn } from '@/lib/utils';
import { REASONING_LEVELS } from '@/lib/reasoning';
import { BASE_URLS, HARNESS_PROVIDERS, MODELS, PROVIDERS, PROVIDER_LABELS, ignoresTemperature, isHarness } from '@/lib/providers';
import HarnessNote from '@/components/HarnessNote';
import PlanNote from '@/components/PlanNote';
import { listMcpServers, type McpServer } from '@/lib/mcp';
import FolderChips from '@/components/FolderChips';
import ModelPicker from '@/components/ModelPicker';
import { folderInfo, type WorkFolder } from '@/lib/folders';
import { ABILITIES, AGENT_TEMPLATES, abilityState, toolsFor, type Ability, type AgentTemplate } from '@/lib/abilities';
import { modelName } from '@/lib/plain';
import { tr } from '@/lib/i18n';

interface Tool { name: string; description?: string; category?: string; default_permission?: 'asks' | 'runs' | 'depends' }
type AgentLevel = 'auto' | 'ask' | 'allow';
type ToolLevel = 'auto' | 'allow' | 'ask' | 'deny';

const AGENT_LEVELS: { value: AgentLevel; label: string; help: string }[] = [
  { value: 'auto', get label() { return tr('ui.agentPage.askBeforeRiskyActions'); }, get help() { return tr('ui.agentPage.deletingOverwritingFilesSendingThings'); } },
  { value: 'ask', get label() { return tr('ui.agentPage.askBeforeEveryAction'); }, get help() { return tr('ui.agentPage.everyToolCallWaitsFor'); } },
  { value: 'allow', get label() { return tr('ui.agentPage.donTAsk'); }, get help() { return tr('ui.agentPage.toolsRunWithoutAskingIncluding'); } },
];

const TOOL_LEVELS: { value: ToolLevel; label: string }[] = [
  { value: 'auto', get label() { return tr('ui.agentPage.default'); } },
  { value: 'allow', get label() { return tr('ui.agentPage.alwaysAllow'); } },
  { value: 'ask', get label() { return tr('ui.agentPage.askFirst'); } },
  { value: 'deny', get label() { return tr('ui.agentPage.never'); } },
];

const DEFAULT_NOTE: Record<string, string> = { asks: 'asks', runs: 'runs', depends: 'depends' };
// Past this many tools an agent looks tools up when it needs them instead of
// carrying every description in every message.
const ON_DEMAND_OVER = 25;

interface AgentRow { id: string; name: string; llm?: AgentData['llm']; automation?: { standing?: { primary?: boolean } } }
interface AgentData {
  id?: string;
  name?: string;
  description?: string;
  system_prompt?: string;
  llm?: { provider?: string; model?: string; temperature?: number; base_url?: string; max_tokens?: number; reasoning_effort?: string; harness_mode?: string };
  tools?: Tool[];
  mcp_servers?: string[];
  folders?: WorkFolder[] | null;
  can_request_folders?: boolean;
  permission_level?: AgentLevel;
  tool_permissions?: Record<string, ToolLevel>;
}

export default function AgentPage() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(agentId);
  const backTo = editing ? `/agents/${agentId}` : '/agents';

  const { data: existing, loading: loadingAgent } = useResource<AgentData>(agentId ? `/agents/${agentId}` : null);
  const { data: toolList } = useResource<Tool[]>('/tools');
  const { data: agentList } = useResource<AgentRow[]>(editing ? null : '/agents');
  const { info: systemInfo } = useSystemInfo();

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [template, setTemplate] = useState('');
  // A new agent thinks with whatever the main assistant uses, until told otherwise.
  const [modelFrom, setModelFrom] = useState('');
  const [showModel, setShowModel] = useState(editing);
  const [moreOpen, setMoreOpen] = useState(false);
  const modelTouched = useRef(false);
  const [provider, setProvider] = useState('google');
  const [model, setModel] = useState('gemini-3.6-flash');
  const [temperature, setTemperature] = useState(0.4);
  const [reasoning, setReasoning] = useState('');
  const [harnessMode, setHarnessMode] = useState('edit');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [maxTokens, setMaxTokens] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [mcpServers, setMcpServers] = useState<McpServer[] | null>(null);
  const [selectedServers, setSelectedServers] = useState<string[]>([]);
  // null: never set, so new chats start with only a private scratch folder.
  const [folders, setFolders] = useState<WorkFolder[] | null>(null);
  const [canRequestFolders, setCanRequestFolders] = useState(true);
  const [workingFolder, setWorkingFolder] = useState('');

  useEffect(() => {
    folderInfo().then((i) => setWorkingFolder(i.working_folder)).catch(() => {});
  }, []);
  const [permissionLevel, setPermissionLevel] = useState<AgentLevel>('auto');
  const [toolPermissions, setToolPermissions] = useState<Record<string, ToolLevel>>({});

  useEffect(() => {
    listMcpServers().then((l) => setMcpServers(Array.isArray(l?.servers) ? l.servers : [])).catch(() => setMcpServers([]));
  }, []);

  useEffect(() => {
    if (!existing) return;
    setName(existing.name || '');
    setDescription(existing.description || '');
    setProvider(existing.llm?.provider || 'google');
    setModel(existing.llm?.model || '');
    setTemperature(existing.llm?.temperature ?? 0.4);
    setReasoning(existing.llm?.reasoning_effort || '');
    setHarnessMode(existing.llm?.harness_mode || 'edit');
    setSystemPrompt(existing.system_prompt || '');
    setSelected(new Set((existing.tools || []).map((t) => t.name)));
    setMaxTokens(existing.llm?.max_tokens ? String(existing.llm.max_tokens) : '');
    setBaseUrl(existing.llm?.base_url || '');
    setSelectedServers(existing.mcp_servers || []);
    setFolders(existing.folders ?? null);
    setCanRequestFolders(existing.can_request_folders ?? true);
    setPermissionLevel(existing.permission_level || 'auto');
    setToolPermissions(existing.tool_permissions || {});
  }, [existing]);

  useEffect(() => {
    if (editing || modelTouched.current || !agentList?.length) return;
    const usual = agentList.find((a) => a.automation?.standing?.primary) ?? agentList[0];
    if (!usual?.llm?.provider || !usual.llm.model) return;
    setProvider(usual.llm.provider);
    setModel(usual.llm.model);
    setBaseUrl(usual.llm.base_url || BASE_URLS[usual.llm.provider] || '');
    setModelFrom(usual.name);
  }, [agentList, editing]);

  const available = useMemo(() => new Set((toolList || []).map((t) => t.name)), [toolList]);

  // A new agent starts able to search, read files and remember.
  const startedWith = useRef(false);
  useEffect(() => {
    if (editing || startedWith.current || available.size === 0) return;
    startedWith.current = true;
    setSelected((cur) => (cur.size ? cur : new Set(toolsFor(['web', 'files', 'recall'], available))));
  }, [available, editing]);

  const applyTemplate = (t: AgentTemplate | null) => {
    setTemplate(t?.id ?? '');
    if (!t) return;
    setName(t.name);
    setDescription(t.purpose);
    setSystemPrompt(t.instructions);
    setSelected(new Set(toolsFor(t.abilities, available)));
  };

  const toggleAbility = (ability: Ability) =>
    setSelected((cur) => {
      const next = new Set(cur);
      const have = ability.tools.filter((n) => available.has(n));
      if (abilityState(ability, cur, available) === 'all') have.forEach((n) => next.delete(n));
      else have.forEach((n) => next.add(n));
      return next;
    });

  const toolsByCategory = useMemo(() => {
    const groups: Record<string, Tool[]> = {};
    for (const t of toolList || []) (groups[t.category || 'general'] ||= []).push(t);
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }, [toolList]);

  // Switching provider resets the model and base URL to that provider's
  // defaults — an old model/endpoint won't work against a different provider.
  const changeProvider = (next: string) => {
    modelTouched.current = true;
    setModelFrom('');
    setProvider(next);
    setModel(MODELS[next]?.[0] || '');
    setBaseUrl(BASE_URLS[next] || '');
  };

  const noTemperature = ignoresTemperature(provider, model);

  const toggleTool = (n: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });

  const setToolLevel = (n: string, level: ToolLevel) =>
    setToolPermissions((cur) => {
      const next = { ...cur };
      if (level === 'auto') delete next[n];
      else next[n] = level;
      return next;
    });

  // What "Default" does for a tool under the agent-wide level.
  const defaultNote = (t: Tool) => {
    if (permissionLevel === 'allow') return 'runs';
    if (permissionLevel === 'ask') return 'asks';
    return DEFAULT_NOTE[t.default_permission || ''] || '';
  };

  const save = async () => {
    if (!name.trim()) return setError(tr('ui.agentPage.giveTheAgentAName'));
    if (!editing && !description.trim() && !systemPrompt.trim()) return setError(tr('ui.agentPage.sayWhatItIsFor'));
    if (!model.trim()) return setError(tr('ui.agentPage.chooseAModel'));
    setError('');
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        ...(agentId ? { id: agentId } : {}),
        name: name.trim(),
        description: description.trim().slice(0, 300),
        // No instructions written: the purpose is the instruction.
        system_prompt: systemPrompt.trim() || editing ? systemPrompt : `You are ${name.trim()}. ${description.trim()}`,
        ...(!editing && selected.size > ON_DEMAND_OVER ? { tool_mode: { on_demand: true } } : {}),
        // api_key is intentionally omitted — the backend resolves it from env.
        llm: {
          provider,
          model: model.trim(),
          temperature,
          reasoning_effort: reasoning,
          ...(isHarness(provider) ? { harness_mode: harnessMode } : {}),
          ...(baseUrl ? { base_url: baseUrl } : {}),
          ...(maxTokens ? { max_tokens: Number(maxTokens) } : {}),
        },
        tools: (toolList || [])
          .filter((t) => selected.has(t.name))
          .map((t) => {
            const rest: Tool = { ...t };
            delete rest.default_permission;
            return rest;
          }),
        mcp_servers: selectedServers,
        folders,
        can_request_folders: canRequestFolders,
        permission_level: permissionLevel,
        // Levels for tools the agent no longer has are dropped, except MCP
        // tools, which aren't in the list but can still be set by name.
        tool_permissions: Object.fromEntries(
          Object.entries(toolPermissions).filter(([n]) => selected.has(n) || !(toolList || []).some((t) => t.name === n)),
        ),
      };
      const response = await api.post('/agents', payload);
      const id = agentId || response.data?.id;
      navigate(id ? `/agents/${id}` : '/agents');
    } catch (e) {
      setError(errorMessage(e, tr('ui.agentPage.couldNotSaveTheAgent')));
    } finally {
      setSaving(false);
    }
  };

  if (editing && loadingAgent) {
    return (
      <div className="flex-1 flex flex-col h-full min-w-0 bg-bg">
        <LoadingState label={tr('ui.agentPage.loadingAgent')} />
      </div>
    );
  }

  return (
    <PageForm
      eyebrow={editing ? 'EDIT AGENT' : 'NEW AGENT'}
      trail={editing
        ? [{ label: tr('ui.agentPage.agents'), to: '/agents' }, { label: name || tr('ui.agentPage.editAgent'), to: backTo }]
        : [{ label: tr('ui.agentPage.agents'), to: '/agents' }]}
      title={editing ? tr('ui.agentPage.editAgent') : tr('ui.agentPage.newAgent')}
      backTo={backTo}
      error={error}
      onSave={save}
      saving={saving}
      saveLabel={editing ? undefined : tr('ui.agentPage.createAgent')}
    >
      {!editing && (
        <Field label={tr('ui.agentPage.startFrom')} composite>
          <div role="group" className="flex flex-wrap gap-2">
            {[null, ...AGENT_TEMPLATES].map((t) => {
              const on = (t?.id ?? '') === template;
              return (
                <button
                  key={t?.id ?? 'blank'}
                  type="button"
                  aria-pressed={on}
                  onClick={() => applyTemplate(t)}
                  className={cn(
                    'min-h-11 rounded-full border px-3.5 text-[13px] transition-colors md:min-h-0 md:py-1.5',
                    on ? 'border-accent bg-accent/10 text-accent-ink' : 'border-line text-fg-dim hover:border-fg-dim hover:text-fg',
                  )}
                >
                  {t ? t.label : tr('ui.agentPage.blank')}
                </button>
              );
            })}
          </div>
        </Field>
      )}

      <Field label={tr('ui.agentPage.name')} required>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('ui.agentPage.researchAssistant')} autoFocus />
      </Field>

      <Field label={tr('ui.agentPage.whatIsItFor')}>
        <Textarea rows={2} maxLength={300} value={description} onChange={(e) => setDescription(e.target.value)} placeholder={tr('ui.agentPage.whatIsItForPlaceholder')} />
        <p className="mt-1.5 text-[12px] text-fg-dim">{tr('ui.agentPage.whatIsItForHint')}</p>
      </Field>

      <Field label={tr('ui.agentPage.howShouldItWork')} hint={tr('ui.agentPage.howShouldItWorkHint')}>
        <Textarea rows={editing ? 8 : 4} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} placeholder={tr('ui.agentPage.howShouldItWorkPlaceholder')} />
      </Field>

      <Field label={tr('ui.agentPage.whatCanItDo')} composite>
        <div role="group" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {ABILITIES.map((ability) => {
            const state = abilityState(ability, selected, available);
            return (
              <label
                key={ability.id}
                className={cn(
                  'flex cursor-pointer items-start gap-2.5 rounded border px-3 py-2.5 transition-colors',
                  state === 'none' ? 'border-line hover:border-fg-dim' : 'border-accent bg-accent/5',
                )}
              >
                <input
                  type="checkbox"
                  checked={state === 'all'}
                  ref={(el) => { if (el) el.indeterminate = state === 'some'; }}
                  onChange={() => toggleAbility(ability)}
                  className="mt-0.5 accent-[var(--accent)]"
                />
                <span className="min-w-0">
                  <span className="block text-[13.5px] font-medium">{ability.label}</span>
                  <span className="block text-[12.5px] text-fg-dim">{ability.help}</span>
                </span>
              </label>
            );
          })}
        </div>
      </Field>
      {selected.has('Bash') && (
        <p className="-mt-1 rounded border border-line bg-panel-2 px-3 py-2 text-[12.5px] text-fg-dim">
          {tr('ui.agentPage.shellCommandsStartInThe', { value: systemInfo && !systemInfo.desktop && tr('ui.agentPage.onASharedServerThe') })}
          <Link to="/settings/access" className="text-accent-ink hover:underline">{tr('ui.agentPage.change')}</Link>
        </p>
      )}
      {selected.has('Coding Agent') && (
        <p className="-mt-1 rounded border border-line bg-panel-2 px-3 py-2 text-[12.5px] text-fg-dim">
          {tr('ui.agentPage.handsCodingJobsToA')}{' '}
          {systemInfo && (systemInfo.coding_agents?.length
            ? <>{tr('ui.agentPage.installed')}{' '}<span className="font-mono text-[12px] text-fg">{systemInfo.coding_agents.join(', ')}</span>.</>
            : tr('ui.agentPage.noneInstalledYetInstallClaude'))}
        </p>
      )}

      {!showModel ? (
        <p className="text-[13px] text-fg-dim">
          {tr('ui.agentPage.thinksWith')}{' '}
          <span className="font-medium text-fg" title={model}>{modelName(model)}</span>
          {modelFrom && <>{', '}{tr('ui.agentPage.sameAsAssistant', { name: modelFrom })}</>}{'. '}
          <button type="button" onClick={() => setShowModel(true)} className="text-accent-ink hover:underline">
            {tr('ui.agentPage.changeModel')}
          </button>
        </p>
      ) : (
      <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label={tr('ui.agentPage.provider')}>
          <Select value={provider} onChange={(e) => changeProvider(e.target.value)}>
            <optgroup label={tr('ui.agentPage.modelProviders')}>
              {PROVIDERS.map((p) => <option key={p} value={p}>{PROVIDER_LABELS[p] || p}</option>)}
            </optgroup>
            <optgroup label={tr('ui.agentPage.codingAgentsOnThisComputer')}>
              {HARNESS_PROVIDERS.map((p) => <option key={p} value={p}>{PROVIDER_LABELS[p]}</option>)}
            </optgroup>
          </Select>
        </Field>
        <Field label={tr('ui.agentPage.model')} required>
          <ModelPicker provider={provider} value={model} onChange={(next) => { modelTouched.current = true; setModelFrom(''); setModel(next); }} />
        </Field>
      </div>
      <PlanNote provider={provider} />
      <HarnessNote provider={provider} mode={harnessMode} onModeChange={setHarnessMode} selectClass="h-11 w-full rounded border border-line bg-bg px-3 text-sm font-normal text-fg md:h-10" />
      </>
      )}

      <button
        type="button"
        aria-expanded={moreOpen}
        onClick={() => setMoreOpen((v) => !v)}
        className="flex min-h-11 w-full items-baseline gap-2 border-t border-line pt-4 text-left text-[13px] font-medium text-fg md:min-h-0"
      >
        <span className={cn('text-fg-dim transition-transform', moreOpen && 'rotate-90')}>▸</span>
        {tr('ui.agentPage.moreOptions')}
        <span className="font-normal text-fg-dim">{tr('ui.agentPage.moreOptionsHint')}</span>
      </button>
      {moreOpen && (
      <>
      <Field label={tr('ui.agentPage.creativity')} hint={noTemperature ? tr('ui.agentPage.notUsed') : temperature.toFixed(2)}>
        <input
          type="range" min={0} max={1} step={0.05} value={temperature}
          onChange={(e) => setTemperature(Number(e.target.value))}
          disabled={noTemperature}
          aria-label={tr('ui.agentPage.temperature_0a90')}
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-panel-2 accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40"
        />
        {noTemperature && (
          <p className="mt-1.5 text-[12px] text-fg-dim">
            {isHarness(provider)
              ? tr('ui.agentPage.itemPicksItsOwnSettings', { item: PROVIDER_LABELS[provider] })
              : provider === 'chatgpt'
              ? tr('ui.agentPage.theChatgptPlanDoesnT')
              : tr('ui.agentPage.thisClaudeModelSetsIts')}
          </p>
        )}
      </Field>

      <Field label={tr('ui.agentPage.thinkingEffort')}>
        <Select aria-label={tr('ui.agentPage.reasoningLevel')} value={reasoning} onChange={(e) => setReasoning(e.target.value)}>
          <option value="">{tr('ui.agentPage.providerDefault')}</option>
          {REASONING_LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
        </Select>
        <p className="mt-1.5 text-[12px] text-fg-dim">
          {tr('ui.agentPage.howHardTheModelThinks')}
        </p>
      </Field>

      <Field label={tr('ui.agentPage.individualTools', { size: selected.size })} composite>
        <div role="group" className="max-h-72 overflow-y-auto rounded border border-line">
          {toolsByCategory.length === 0 ? (
            <div className="px-3 py-4 font-mono text-[11px] text-fg-dim">{tr('ui.agentPage.noToolsAvailable')}</div>
          ) : (
            toolsByCategory.map(([cat, tools]) => (
              <div key={cat} className="border-b border-line last:border-b-0">
                <div className="bg-panel-2 px-3 py-1.5 font-mono text-[10px] tracking-[0.14em] text-fg-dim">{cat.toUpperCase()}</div>
                {tools.map((t) => {
                  const level = toolPermissions[t.name] || 'auto';
                  const note = defaultNote(t);
                  return (
                    <div key={t.name} className="flex items-start gap-2 px-3 py-2 hover:bg-panel-2">
                      <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2.5">
                        <input type="checkbox" checked={selected.has(t.name)} onChange={() => toggleTool(t.name)}
                          className="mt-0.5 accent-[var(--accent)]" />
                        <div className="min-w-0">
                          <div className="font-mono text-[12px]">{t.name}</div>
                          {t.description && <div className="truncate text-[12px] text-fg-dim">{t.description}</div>}
                        </div>
                      </label>
                      {selected.has(t.name) && (
                        <Select
                          aria-label={tr('ui.agentPage.permissionForName', { name: t.name })}
                          value={level}
                          onChange={(e) => setToolLevel(t.name, e.target.value as ToolLevel)}
                          className={cn(
                            'h-9 w-36 shrink-0 px-2 text-[12px] md:h-8',
                            level === 'deny' && 'text-danger-ink',
                            level !== 'auto' && 'border-accent',
                          )}
                        >
                          {TOOL_LEVELS.map((l) => (
                            <option key={l.value} value={l.value}>
                              {l.value === 'auto' && note ? tr('ui.agentPage.defaultNote', { note }) : l.label}
                            </option>
                          ))}
                        </Select>
                      )}
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </Field>

      <Field label={tr('ui.agentPage.approvals')}>
        <Select aria-label={tr('ui.agentPage.permissionLevel')} value={permissionLevel} onChange={(e) => setPermissionLevel(e.target.value as AgentLevel)}>
          {AGENT_LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
        </Select>
        <p className={cn('mt-1.5 text-[12px]', permissionLevel === 'allow' ? 'text-danger-ink' : 'text-fg-dim')}>
          {tr('ui.agentPage.helpSetASingleTool', { help: AGENT_LEVELS.find((l) => l.value === permissionLevel)?.help })}
        </p>
      </Field>

      <Field label={tr('ui.agentPage.connectedServers', { length: selectedServers.length })} composite>
        <div role="group" className="rounded border border-line">
          {mcpServers === null ? (
            <div className="px-3 py-3 font-mono text-[11px] text-fg-dim">{tr('ui.agentPage.loading')}</div>
          ) : mcpServers.length === 0 && selectedServers.length === 0 ? (
            <div className="px-3 py-3 text-[12.5px] text-fg-dim">
              {tr('ui.agentPage.noMcpServersYet')}{' '}<Link to="/settings/access#mcp-servers" className="text-accent-ink hover:underline">{tr('ui.agentPage.addOneInSettings')}</Link>
            </div>
          ) : (
            <>
              {mcpServers.map((s) => {
                const checked = selectedServers.includes(s.ref) || selectedServers.includes(s.name);
                return (
                  <label key={s.ref} className="flex cursor-pointer items-start gap-2.5 border-b border-line px-3 py-2 last:border-b-0 hover:bg-panel-2">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => setSelectedServers((cur) =>
                        checked ? cur.filter((r) => r !== s.ref && r !== s.name) : [...cur, s.ref])}
                      className="mt-0.5 accent-[var(--accent)]"
                    />
                    <div className="min-w-0">
                      <div className="font-mono text-[12px]">
                        {s.name}
                        {s.shared && <span className="ml-2 font-sans text-[11px] text-fg-dim">{tr('ui.agentPage.shared')}</span>}
                        {!s.enabled && <span className="ml-2 font-sans text-[11px] text-fg-dim">{tr('ui.agentPage.off')}</span>}
                      </div>
                      {s.description && <div className="truncate text-[12px] text-fg-dim">{s.description}</div>}
                    </div>
                  </label>
                );
              })}
              {selectedServers
                .filter((r) => !mcpServers.some((s) => s.ref === r || s.name === r))
                .map((r) => (
                  <div key={r} className="flex items-center justify-between gap-2 border-b border-line px-3 py-2 text-[12px] last:border-b-0">
                    <span className="text-fg-dim">
                      <span className="font-mono">{r}</span>{' '}{tr('ui.agentPage.isnTAvailableAnyMore')}
                    </span>
                    <button type="button" onClick={() => setSelectedServers((cur) => cur.filter((x) => x !== r))}
                      className="text-danger-ink hover:underline">
                      {tr('ui.agentPage.remove')}
                    </button>
                  </div>
                ))}
            </>
          )}
        </div>
        <p className="mt-1.5 text-[12px] text-fg-dim">
          {tr('ui.agentPage.theAgentCanListAnd')}
        </p>
      </Field>

      <Field label={tr('ui.agentPage.workingFolders')} hint={tr('ui.agentPage.copiedIntoEachNewChat')} composite>
        <div className="rounded border border-line px-3 py-2.5">
          {folders === null ? (
            <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-fg-dim">
              <span>
                {tr('ui.agentPage.noDefaultFoldersEachNew')}
              </span>
              <button type="button" className="text-accent-ink hover:underline"
                onClick={() => setFolders(workingFolder ? [{ path: workingFolder, access: 'write', primary: true }] : [])}>
                {tr('ui.agentPage.chooseFolders')}
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <FolderChips folders={folders} onChange={setFolders} emptyLabel="none: each chat gets a private scratch folder" />
              <button type="button" className="self-start text-[12px] text-fg-dim hover:text-fg hover:underline"
                onClick={() => setFolders(null)}>
                {tr('ui.agentPage.clearDefaultFolders')}
              </button>
            </div>
          )}
        </div>
        <label className="mt-2 flex cursor-pointer items-start gap-2.5 text-[12.5px]">
          <input type="checkbox" checked={canRequestFolders} onChange={(e) => setCanRequestFolders(e.target.checked)}
            className="mt-0.5 accent-[var(--accent)]" />
          <span>
            {tr('ui.agentPage.letTheAgentAskFor')}
            <span className="block text-fg-dim">{tr('ui.agentPage.itCanAskForA')}</span>
          </span>
        </label>
        <p className="mt-1.5 text-[12px] text-fg-dim">
          {tr('ui.agentPage.readOnlyFoldersCanBe')}
        </p>
      </Field>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label={tr('ui.agentPage.replyLengthLimit')} hint={tr('ui.agentPage.blankProviderDefault')}>
            <Input type="number" value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} placeholder="8192" />
          </Field>
          <Field label={tr('ui.agentPage.customAddress')} hint={tr('ui.agentPage.overrideEndpointEGLocal')}>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" />
            {baseUrl.trim() && (
              <p className="mt-1.5 text-[12px] leading-relaxed text-fg-dim">
                {tr('ui.agentPage.yourStoredProviderKeyIs')}
              </p>
            )}
          </Field>
        </div>
      </>
      )}
    </PageForm>
  );
}
