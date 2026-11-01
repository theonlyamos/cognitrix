import { useId, useMemo, useState } from 'react';
import ModelPicker from '@/components/ModelPicker';
import { MODELS, PROVIDERS, PROVIDER_LABELS } from '@/lib/providers';
import type { AgentLevel, CodingMode, ReviewerMode, TabProps, ToolInfo, ToolLevel } from '@/lib/agentDraft';
import { Card, Pill, buttonClass, inputClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';

const LEVELS: { value: AgentLevel; label: string; help: string }[] = [
  { value: 'auto', label: 'Only risky actions', help: 'Reading and searching run on their own. Deleting, overwriting, sending things out and most shell commands ask first.' },
  { value: 'ask', label: 'Ask for everything', help: 'Every action waits for you, except questions to you. Good for an agent you are still getting to know.' },
  { value: 'allow', label: 'Allow everything', help: "Tools run without asking, including deletes and sends. Full shell commands and coding hand-offs still ask." },
];

const TOOL_LEVELS: { value: ToolLevel; label: string }[] = [
  { value: 'auto', label: 'Agent default' },
  { value: 'allow', label: 'Always allow' },
  { value: 'ask', label: 'Ask first' },
  { value: 'deny', label: 'Never' },
];

// Tools that need a person to approve each call, whatever the levels say.
const ALWAYS_ASKS = new Set(['Coding Agent', 'Request Folder']);

const MODE_LABELS: Record<string, string> = {
  off: 'Off',
  unattended: 'Only for unattended task runs',
  always: 'Always',
};

export default function ToolsTab({ draft, update, options, catalog, agent }: TabProps) {
  const ids = { mode: useId(), provider: useId(), model: useId(), max: useId(), search: useId(), coding: useId() };
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState(false);

  const known = useMemo(() => {
    const map = new Map<string, ToolInfo>();
    for (const t of agent.tools || []) map.set(t.name, t);
    for (const t of catalog) map.set(t.name, t);
    return map;
  }, [agent.tools, catalog]);

  const defaultNote = (t?: ToolInfo) => {
    if (draft.permissionLevel === 'allow') return 'runs';
    if (draft.permissionLevel === 'ask') return 'asks';
    const notes: Record<string, string> = { asks: 'asks', runs: 'runs', depends: 'depends' };
    return notes[t?.default_permission || ''] || '';
  };

  const q = query.trim().toLowerCase();
  const matches = (t: ToolInfo) => !q || t.name.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q);
  const grouped = (names: string[]) => {
    const groups: Record<string, ToolInfo[]> = {};
    for (const name of names) {
      const t = known.get(name) || { name };
      if (!matches(t)) continue;
      (groups[t.category || 'general'] ||= []).push(t);
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  };
  const mine = grouped(draft.tools);
  const others = grouped(catalog.map((t) => t.name).filter((n) => !draft.tools.includes(n)));

  const setLevel = (name: string, level: ToolLevel) => {
    const next = { ...draft.toolPermissions };
    if (level === 'auto') delete next[name];
    else next[name] = level;
    update({ toolPermissions: next });
  };
  const removeTool = (name: string) => update({ tools: draft.tools.filter((n) => n !== name) });
  const addTool = (name: string) => update({ tools: [...draft.tools, name] });

  const counts = {
    ask: draft.tools.filter((n) => draft.toolPermissions[n] === 'ask').length,
    never: draft.tools.filter((n) => draft.toolPermissions[n] === 'deny').length,
  };

  const app = options?.reviewer;
  const reviewerOn = (draft.reviewerMode || app?.mode || 'off') !== 'off';
  const hasCoding = draft.tools.includes('Coding Agent');
  const clis = options?.coding_clis || [];

  return (
    <div className="flex flex-col gap-[18px]">
      <Card title="When to ask you" description="Applies to every tool unless you set one below.">
        <div role="radiogroup" aria-label="When to ask you" className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
          {LEVELS.map((l) => {
            const on = draft.permissionLevel === l.value;
            return (
              <button key={l.value} type="button" role="radio" aria-checked={on} onClick={() => update({ permissionLevel: l.value })}
                className={cn('flex flex-col justify-start rounded-lg border p-3.5 text-left transition-colors', on ? 'border-accent bg-accent/10' : 'border-line bg-bg hover:border-fg-dim')}>
                <span className="block text-[14px] font-semibold">{l.label}</span>
                <span className="mt-1 block text-[12.5px] leading-snug text-fg-dim">{l.help}</span>
              </button>
            );
          })}
        </div>
        <p className="mt-3 text-[12.5px] text-fg-dim">Full shell commands, coding agent hand-offs and folder requests always ask, whatever you pick.</p>
      </Card>

      <Card title="Second-model check">
        <p className="-mt-2 text-[13px] leading-relaxed text-fg-dim">
          Before a risky action reaches you, another model reads the goal and the action. It allows it, blocks it, or passes it to you.
          If it fails or is unsure, you are asked. Actions you set to “Ask first” always come to you.
        </p>
        <div className="mt-3.5 grid grid-cols-1 gap-x-[18px] gap-y-3.5 md:grid-cols-3">
          <label htmlFor={ids.mode} className="flex flex-col gap-1.5 text-[13px] font-medium">
            Use it
            <select id={ids.mode} value={draft.reviewerMode} onChange={(e) => update({ reviewerMode: e.target.value as ReviewerMode })} className={inputClass}>
              <option value="">App setting ({(MODE_LABELS[app?.mode || 'off'] || 'Off').toLowerCase()})</option>
              {Object.entries(MODE_LABELS).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </select>
          </label>
          {reviewerOn && (
            <>
              <label htmlFor={ids.provider} className="flex flex-col gap-1.5 text-[13px] font-medium">
                Checking model provider
                <select id={ids.provider} value={draft.reviewerProvider}
                  onChange={(e) => update({ reviewerProvider: e.target.value, reviewerModel: MODELS[e.target.value]?.[0] || '' })}
                  className={inputClass}>
                  <option value="">App setting ({PROVIDER_LABELS[app?.provider || ''] || app?.provider || 'default'}{app?.model ? ` · ${app.model}` : ''})</option>
                  {PROVIDERS.map((p) => <option key={p} value={p}>{PROVIDER_LABELS[p] || p}</option>)}
                </select>
              </label>
              <label htmlFor={ids.max} className="flex flex-col gap-1.5 text-[13px] font-medium">
                It may allow up to
                <select id={ids.max} value={draft.reviewerMaxAllow} onChange={(e) => update({ reviewerMaxAllow: e.target.value as '' | 'medium' | 'high' })} className={inputClass}>
                  <option value="">App setting ({app?.max_allow || 'medium'} risk)</option>
                  <option value="medium">Medium risk</option>
                  <option value="high">High risk</option>
                </select>
              </label>
              {draft.reviewerProvider && (
                <div className="flex flex-col gap-1.5 text-[13px] font-medium md:col-span-3">
                  <label htmlFor={ids.model}>Checking model</label>
                  <ModelPicker id={ids.model} provider={draft.reviewerProvider} value={draft.reviewerModel}
                    onChange={(reviewerModel) => update({ reviewerModel })} className="font-normal md:max-w-[420px]" />
                  <span className="text-[12px] font-normal text-fg-dim">A small, fast model is usually enough.</span>
                </div>
              )}
            </>
          )}
        </div>
      </Card>

      <Card
        title="Tools"
        description={`${draft.tools.length} on · ${counts.ask} ask first · ${counts.never} never`}
        aside={(
          <>
            <label htmlFor={ids.search} className="sr-only">Search tools</label>
            <input id={ids.search} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tools"
              className={cn(inputClass, 'w-full text-[13px] sm:w-[200px]')} />
            <button type="button" onClick={() => setAdding((v) => !v)} aria-expanded={adding} className={buttonClass}>
              {adding ? 'Done adding' : '+ Add tools'}
            </button>
          </>
        )}
      >
        {adding && (
          <div className="mb-4 max-h-80 overflow-y-auto rounded-lg border border-dashed border-line">
            {others.length === 0 ? (
              <p className="px-3.5 py-3 text-[13px] text-fg-dim">{q ? 'No other tools match.' : 'Every tool is already on.'}</p>
            ) : others.map(([cat, tools]) => (
              <div key={cat}>
                <div className="bg-panel-2 px-3.5 py-1.5 font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{cat.toUpperCase()}</div>
                {tools.map((t) => (
                  <div key={t.name} className="flex items-center gap-3 border-t border-line px-3.5 py-2 first:border-t-0">
                    <div className="min-w-0 flex-1">
                      <div className="font-mono text-[13px]">{t.name}</div>
                      {t.description && <div className="truncate text-[12.5px] text-fg-dim">{t.description}</div>}
                    </div>
                    <button type="button" onClick={() => addTool(t.name)} className={buttonClass} aria-label={`Add ${t.name}`}>Add</button>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        <div className="overflow-hidden rounded-lg border border-line">
          <div className="hidden grid-cols-[minmax(0,1fr)_190px_36px] gap-3 bg-panel-2 px-3.5 py-2 text-[12px] text-fg-dim sm:grid">
            <span>Tool</span><span>Permission</span><span />
          </div>
          {mine.length === 0 ? (
            <p className="border-t border-line px-3.5 py-3 text-[13px] text-fg-dim sm:border-t">{q ? 'No tools match.' : 'No tools yet. Add some so the agent can act.'}</p>
          ) : mine.map(([cat, tools]) => (
            <div key={cat}>
              <div className="border-t border-line px-3.5 pb-1 pt-2 font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{cat.toUpperCase()}</div>
              {tools.map((t) => {
                const level = draft.toolPermissions[t.name] || 'auto';
                const note = defaultNote(known.get(t.name));
                return (
                  <div key={t.name} className="grid grid-cols-[minmax(0,1fr)_36px] items-center gap-x-3 gap-y-2 border-t border-line px-3.5 py-2.5 sm:grid-cols-[minmax(0,1fr)_190px_36px]">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 font-mono text-[13px]">
                        {t.name}
                        {ALWAYS_ASKS.has(t.name) && <Pill tone="warn">always asks</Pill>}
                      </div>
                      {t.description && <div className="truncate text-[12.5px] text-fg-dim">{t.description}</div>}
                    </div>
                    <select aria-label={`Permission for ${t.name}`} value={level} onChange={(e) => setLevel(t.name, e.target.value as ToolLevel)}
                      className={cn(inputClass, 'col-span-2 row-start-2 h-10 px-2 text-[13px] sm:col-span-1 sm:row-start-auto md:h-9',
                        level === 'deny' && 'text-danger-ink', level !== 'auto' && 'border-accent')}>
                      {TOOL_LEVELS.map((l) => (
                        <option key={l.value} value={l.value}>{l.value === 'auto' && note ? `Agent default (${note})` : l.label}</option>
                      ))}
                    </select>
                    <button type="button" onClick={() => removeTool(t.name)} aria-label={`Remove ${t.name}`}
                      className="col-start-2 row-start-1 grid h-9 w-9 place-items-center rounded-md border border-line text-[12px] text-fg-dim hover:text-fg sm:col-start-auto sm:row-start-auto sm:h-[30px] sm:w-[30px]">
                      ✕
                    </button>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </Card>

      <Card title="Coding agent hand-off">
        <p className="-mt-2 text-[13px] text-fg-dim">
          The Coding Agent tool passes a job to a coding CLI on the computer running Cognitrix. It works in the chat's primary folder and always asks you first.
          {!hasCoding && ' Add the Coding Agent tool above to use it.'}
        </p>
        <div className={cn('mt-3.5', !hasCoding && 'opacity-60')}>
          <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
            <input type="checkbox" checked={draft.codingClis === null} disabled={!hasCoding}
              onChange={(e) => update({ codingClis: e.target.checked ? null : clis.filter((c) => c.installed).map((c) => c.key) })}
              className="h-4 w-4 accent-[var(--accent)]" />
            Use any installed coding CLI
          </label>
          <div className="mt-3 overflow-hidden rounded-lg border border-line">
            {clis.map((cli) => {
              const on = draft.codingClis === null ? cli.installed : draft.codingClis.includes(cli.key);
              return (
                <label key={cli.key} className={cn('flex items-center gap-3 border-t border-line px-3.5 py-2.5 text-[14px] first:border-t-0',
                  draft.codingClis === null || !hasCoding ? 'cursor-not-allowed' : 'cursor-pointer')}>
                  <input type="checkbox" checked={on} disabled={draft.codingClis === null || !hasCoding}
                    onChange={(e) => update({ codingClis: e.target.checked ? [...(draft.codingClis || []), cli.key] : (draft.codingClis || []).filter((k) => k !== cli.key) })}
                    className="h-4 w-4 accent-[var(--accent)]" />
                  <span className="flex-1">{cli.label} <span className="font-mono text-[12px] text-fg-dim">{cli.command}</span></span>
                  <span className={cn('text-[12.5px]', cli.installed ? 'text-ok' : 'text-fg-dim')}>{cli.installed ? 'found' : 'not installed'}</span>
                </label>
              );
            })}
          </div>
          <label htmlFor={ids.coding} className="mt-3.5 flex max-w-[360px] flex-col gap-1.5 text-[13px] font-medium">
            Most it may do
            <select id={ids.coding} value={draft.codingMaxMode} disabled={!hasCoding}
              onChange={(e) => update({ codingMaxMode: e.target.value as CodingMode })} className={inputClass}>
              <option value="">Anything (edit files and run commands)</option>
              <option value="read_only">Read only</option>
              <option value="edit">Edit files</option>
            </select>
          </label>
        </div>
      </Card>
    </div>
  );
}
