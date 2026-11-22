import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { Toggle } from '@/pages/Notifications';
import { timeAgo } from '@/lib/agentDraft';
import { cn } from '@/lib/utils';
import {
  addMemory, approveMemory, convertLegacyMemory, deleteAllMemories, deleteLegacyMemory, deleteMemory, exportMemories,
  importMemories, legacyMemoryCount, listMemories, saveMemorySettings, settlePendingMemories, updateMemory,
  type ImportResult, type MemoryList, type MemoryScope, type SavedMemory,
} from '@/lib/memories';
import { Group, Segmented, SettingRow, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';
import { AboutYouCard, DreamSection, MemoryHistory } from '@/components/settings/MemoryDream';
import { ReviewSection } from '@/components/settings/MemoryReview';

interface AgentLite { id: string; name: string }

/** Settings › Memory: what agents remember about you, and the switches for it. */
export function MemorySettings() {
  const location = useLocation();
  const highlight = new URLSearchParams(location.search).get('memory');
  const [data, setData] = useState<MemoryList | null>(null);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<string>('all');
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState(false);
  const [legacy, setLegacy] = useState(0);
  const [confirmAll, setConfirmAll] = useState(false);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const load = () => listMemories().then((d) => { setData(d); setReloadKey((k) => k + 1); })
    .catch((e) => setLoadError(errorMessage(e, tr('ui.settings.memory.couldNotLoadMemories'))));
  const reload = useCallback(() => { void load(); }, []);

  useEffect(() => {
    void load();
    api.get<AgentLite[]>('/agents').then((r) => setAgents((r.data || []).map((a) => ({ id: a.id, name: a.name })))).catch(() => {});
    legacyMemoryCount().then(setLegacy).catch(() => {});
  }, []);

  const pending = useMemo(() => (data ? data.memories.filter((m) => m.pending) : []), [data]);
  const saved = useMemo(() => (data ? data.memories.filter((m) => !m.pending) : []), [data]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return saved.filter((m) => (
      (filter === 'all' || (filter === 'global' ? m.scope === 'global' : m.agent_id === filter))
      && (!q || m.content.toLowerCase().includes(q))
    ));
  }, [saved, filter, query]);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!data) return <p className="text-[13px] text-fg-dim">{tr('ui.settings.memory.loading')}</p>;

  /** Runs fn, then reloads. Resolves true on success, false after showing the error. */
  const run = async (fn: () => Promise<unknown>, fallback: string): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      return true;
    } catch (e) {
      setError(errorMessage(e, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const settings = data.settings;
  const agentIds = new Set(saved.filter((m) => m.agent_id).map((m) => m.agent_id as string));

  const proposedNotice = (r: ImportResult, what: string) => {
    const parts = [`${r.proposed} ${r.proposed === 1 ? 'memory' : 'memories'} from ${what} waiting for your OK below.`];
    if (r.duplicates) parts.push(tr('ui.settings.memory.duplicatesAlreadySaved', { duplicates: r.duplicates }));
    if (r.skipped.length) parts.push(tr('ui.settings.memory.lengthLeftOutSecretsToo', { length: r.skipped.length }));
    if (!r.proposed && !r.duplicates) parts.splice(0, 1, tr('ui.settings.memory.nothingWorthKeepingWasFound', { what }));
    setNotice(parts.join(' '));
  };
  const chips = [
    { id: 'all', label: tr('ui.settings.memory.all') },
    { id: 'global', label: tr('ui.settings.memory.global') },
    ...agents.filter((a) => agentIds.has(a.id)).map((a) => ({ id: a.id, label: a.name })),
  ];

  return (
    <div className="space-y-2">
      <Group>
        <SettingRow label={tr('ui.settings.memory.useSavedMemories')} detail={tr('ui.settings.memory.agentsReadTheNotesBelow')}>
          <Toggle label={tr('ui.settings.memory.useSavedMemories')} checked={settings.enabled} disabled={busy}
            onChange={(enabled) => void run(() => saveMemorySettings({ enabled }), tr('ui.settings.memory.couldNotChangeTheSetting'))} />
        </SettingRow>
        <SettingRow label={tr('ui.settings.memory.referenceChatHistory')} detail={tr('ui.settings.memory.agentsCanSearchAndRead')}>
          <Toggle label={tr('ui.settings.memory.referenceChatHistory')} checked={settings.history_enabled} disabled={busy}
            onChange={(history_enabled) => void run(() => saveMemorySettings({ history_enabled }), tr('ui.settings.memory.couldNotChangeTheSetting'))} />
        </SettingRow>
        <SettingRow
          label={tr('ui.settings.memory.saveNewMemories')}
          detail={settings.auto_save === 'auto'
            ? tr('ui.settings.memory.agentsSaveWhatSeemsWorth_e50c')
            : settings.auto_save === 'ask'
              ? tr('ui.settings.memory.agentsProposeMemoriesAndYou')
              : tr('ui.settings.memory.agentsOnlySaveSomethingWhen')}
        >
          <Segmented
            label={tr('ui.settings.memory.saveNewMemories')}
            value={settings.auto_save}
            options={[{ value: 'auto', label: tr('ui.settings.memory.automatically') }, { value: 'ask', label: tr('ui.settings.memory.askMeFirst') }, { value: 'explicit', label: tr('ui.settings.memory.whenIAsk') }]}
            onChange={(auto_save) => void run(() => saveMemorySettings({ auto_save }), tr('ui.settings.memory.couldNotChangeTheSetting'))}
          />
        </SettingRow>
      </Group>

      {notice && (
        <p role="status" className="!mt-4 rounded-lg border border-line bg-panel px-3 py-2 text-[12.5px] text-fg-dim">
          {notice} <button type="button" className="ml-1 text-accent-ink hover:underline" onClick={() => setNotice(null)}>{tr('ui.settings.memory.dismiss')}</button>
        </p>
      )}

      <AboutYouCard disabled={!settings.enabled} />
      <DreamSection enabled={settings.dream_enabled !== false} memoryOn={settings.enabled} onChanged={reload} />
      <ReviewSection enabled={settings.review_enabled !== false} onChanged={reload} />

      {pending.length > 0 && (
        <section aria-label={tr('ui.settings.memory.memoriesWaitingForYourOk')}>
          <div className="!mt-6 flex flex-wrap items-center gap-2">
            <SubHeading className="!mt-0 flex-1">{tr('ui.settings.memory.waitingForYourOkLength', { length: pending.length })}</SubHeading>
            <button type="button" disabled={busy} className={quietButtonClass}
              onClick={() => void run(() => settlePendingMemories(false), tr('ui.settings.memory.couldNotDiscardThem'))}>{tr('ui.settings.memory.discardAll')}</button>
            <button type="button" disabled={busy} className={buttonClass}
              onClick={() => void run(() => settlePendingMemories(true), tr('ui.settings.memory.couldNotSaveThem'))}>{tr('ui.settings.memory.saveAll')}</button>
          </div>
          <p className="mb-2 mt-1 text-[12px] text-fg-dim">{tr('ui.settings.memory.agentsDonTSeeThese')}</p>
          <ul className="divide-y divide-line rounded-[12px] border border-accent/40 bg-panel">
            {pending.map((m) => (
              <PendingRow key={m.id} memory={m} busy={busy} highlighted={m.id === highlight}
                replaced={m.replaces ? saved.find((x) => x.id === m.replaces)?.content : undefined}
                onApprove={() => void run(() => approveMemory(m.id), tr('ui.settings.memory.couldNotSaveTheMemory'))}
                onDiscard={() => void run(() => deleteMemory(m.id), tr('ui.settings.memory.couldNotDiscardTheMemory'))} />
            ))}
          </ul>
        </section>
      )}

      <SubHeading className="!mt-6">{tr('ui.settings.memory.savedMemoriesLength', { length: saved.length })}</SubHeading>

      <div className={cn('space-y-3', !settings.enabled && 'opacity-60')}>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex h-9 min-w-[200px] flex-1 items-center gap-2 rounded-lg border border-line bg-bg px-2.5 text-fg-dim focus-within:border-accent">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
            </svg>
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={tr('ui.settings.memory.searchMemories')}
              aria-label={tr('ui.settings.memory.searchMemories')} className="w-full bg-transparent text-[13px] text-fg outline-none placeholder:text-fg-dim" />
          </label>
          <button type="button" onClick={() => { setError(null); setAdding(true); }} className={primaryButtonClass}>{tr('ui.settings.memory.add')}</button>
        </div>
        {chips.length > 2 && (
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={tr('ui.settings.memory.showMemoriesFor')}>
            {chips.map((c) => (
              <button key={c.id} type="button" role="radio" aria-checked={filter === c.id} onClick={() => setFilter(c.id)}
                className={cn('h-8 rounded-full border px-3 text-[12px] md:h-7',
                  filter === c.id ? 'border-accent bg-accent/10 text-fg' : 'border-line text-fg-dim hover:text-fg')}>
                {c.label}
              </button>
            ))}
          </div>
        )}

        {adding && (
          <MemoryForm agents={agents} busy={busy} maxChars={data.max_chars}
            onCancel={() => setAdding(false)}
            onSave={(content, scope, agentId) => void run(async () => {
              await addMemory({ content, scope, agent_id: agentId });
              setAdding(false);
            }, tr('ui.settings.memory.couldNotSaveTheMemory'))} />
        )}

        {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}

        {importing && (
          <ImportForm busy={busy} onCancel={() => setImporting(false)}
            onImport={(text) => void run(async () => {
              const result = await importMemories(text);
              setImporting(false);
              proposedNotice(result, 'your import');
            }, tr('ui.settings.memory.couldNotImportThoseMemories'))} />
        )}

        {saved.length === 0 ? (
          <p className="rounded-[10px] border border-line bg-panel px-3.5 py-4 text-[13px] leading-relaxed text-fg-dim">
            {tr('ui.settings.memory.nothingSavedYetTellAn_b1ac')}
          </p>
        ) : shown.length === 0 ? (
          <p className="px-1 text-[13px] text-fg-dim">{tr('ui.settings.memory.noMemoriesMatch')}</p>
        ) : (
          <ul className="divide-y divide-line rounded-[12px] border border-line bg-panel">
            {shown.map((m) => (
              <MemoryRow key={m.id} memory={m} agents={agents} busy={busy} highlighted={m.id === highlight} maxChars={data.max_chars}
                onSave={(content, scope, agentId) => run(() => updateMemory(m.id, { content, scope, agent_id: agentId }), tr('ui.settings.memory.couldNotSaveTheMemory'))}
                onPin={() => void run(() => updateMemory(m.id, { pinned: !m.pinned }), tr('ui.settings.memory.couldNotPinTheMemory'))}
                onDelete={() => void run(() => deleteMemory(m.id), tr('ui.settings.memory.couldNotDeleteTheMemory'))} />
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-3 text-[12.5px]">
        <button type="button" className="text-fg-dim hover:text-fg hover:underline" onClick={() => { setError(null); setImporting(true); }}>
          {tr('ui.settings.memory.import_4834')}
        </button>
        <MemoryHistory reloadKey={reloadKey} onChanged={reload} />
        {saved.length > 0 && (
          <>
            <button type="button" className="text-fg-dim hover:text-fg hover:underline"
              onClick={() => void exportMemories('markdown').catch((e) => setError(errorMessage(e, tr('ui.settings.memory.couldNotExport'))))}>{tr('ui.settings.memory.exportMarkdown')}</button>
            <button type="button" className="text-fg-dim hover:text-fg hover:underline"
              onClick={() => void exportMemories('json').catch((e) => setError(errorMessage(e, tr('ui.settings.memory.couldNotExport'))))}>{tr('ui.settings.memory.exportJson')}</button>
          </>
        )}
        {legacy > 0 && (
          <button type="button" disabled={busy} className="text-fg-dim hover:text-fg hover:underline disabled:opacity-60"
            title={tr('ui.settings.memory.aModelReadsTheOld')}
            onClick={() => void run(async () => proposedNotice(await convertLegacyMemory(), 'your old automatic memory'), tr('ui.settings.memory.couldNotConvertTheOld'))}>
            {busy ? tr('ui.settings.memory.working') : tr('ui.settings.memory.turnOldMemoryIntoSaved')}
          </button>
        )}
        {legacy > 0 && (
          <button type="button" disabled={busy} className="text-fg-dim hover:text-fg hover:underline disabled:opacity-60"
            onClick={() => void run(async () => { await deleteLegacyMemory(); setLegacy(0); }, tr('ui.settings.memory.couldNotDeleteTheOld'))}
            title={tr('ui.settings.memory.beforeSavedMemoriesAgentsKept')}>
            {tr('ui.settings.memory.deleteOldAutomaticMemoryLegacy', { legacy })}
          </button>
        )}
        <span className="flex-1" />
        {data.memories.length > 0 && (confirmAll ? (
          <span className="flex items-center gap-2">
            <span className="text-danger-ink">{tr('ui.settings.memory.deleteAllLengthMemories', { length: data.memories.length })}</span>
            <button type="button" disabled={busy} className="font-medium text-danger-ink hover:underline"
              onClick={() => void run(async () => { await deleteAllMemories(); setConfirmAll(false); }, tr('ui.settings.memory.couldNotDeleteMemories'))}>
              {tr('ui.settings.memory.deleteAll')}
            </button>
            <button type="button" className="text-fg-dim hover:text-fg" onClick={() => setConfirmAll(false)}>{tr('ui.settings.memory.cancel')}</button>
          </span>
        ) : (
          <button type="button" className="text-danger-ink hover:underline" onClick={() => setConfirmAll(true)}>{tr('ui.settings.memory.deleteAllMemories')}</button>
        ))}
      </div>
    </div>
  );
}

function PendingRow({ memory, busy, highlighted, replaced, onApprove, onDiscard }: {
  memory: SavedMemory;
  busy: boolean;
  highlighted: boolean;
  replaced?: string;
  onApprove: () => void;
  onDiscard: () => void;
}) {
  return (
    <li className={cn('flex flex-wrap items-start gap-x-3 gap-y-1.5 px-3.5 py-3', highlighted && 'bg-accent/10')}>
      <div className="min-w-0 flex-1 basis-[240px]">
        <div className="text-[13.5px] leading-snug">{memory.content}</div>
        <div className="mt-1 text-[11.5px] text-fg-dim">
          {replaced && <>{tr('ui.settings.memory.replacesReplaced', { replaced })}{' '}</>}
          {memory.source === 'import' ? <>{tr('ui.settings.memory.importedTimeago', { timeAgo: timeAgo(memory.created) })}</> : origin(memory)}
        </div>
      </div>
      <span className={cn('mt-0.5 rounded px-1.5 py-0.5 font-mono text-[10px] tracking-[0.06em]',
        memory.scope === 'global' ? 'bg-accent/15 text-accent-ink' : 'bg-panel-2 text-fg-dim')}>
        {scopeLabel(memory)}
      </span>
      <div className="flex items-center gap-0.5">
        <button type="button" onClick={onDiscard} disabled={busy} className={cn(quietButtonClass, 'hover:text-danger-ink')}>{tr('ui.settings.memory.discard')}</button>
        <button type="button" onClick={onApprove} disabled={busy} className={buttonClass}>{tr('ui.settings.memory.save')}</button>
      </div>
    </li>
  );
}

function ImportForm({ busy, onImport, onCancel }: { busy: boolean; onImport: (text: string) => void; onCancel: () => void }) {
  const [text, setText] = useState('');
  return (
    <form className="space-y-2.5 rounded-[10px] border border-line bg-bg p-3" aria-label={tr('ui.settings.memory.importMemories')}
      onSubmit={(e) => { e.preventDefault(); if (text.trim()) onImport(text.trim()); }}>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        {tr('ui.settings.memory.pasteWhatAnotherAssistantRemembers')}
      </p>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} autoFocus aria-label={tr('ui.settings.memory.memoriesToImport')}
        placeholder={tr('ui.settings.memory.prefersShortAnswersWorksOn')}
        className="w-full resize-y rounded-lg border border-line bg-panel p-2.5 text-[13px] text-fg placeholder:text-fg-dim focus:border-accent focus:outline-none" />
      <div className="flex items-center gap-2">
        <span className="flex-1 text-[11.5px] text-fg-dim">{tr('ui.settings.memory.yourMainAgentSModel')}</span>
        <button type="button" onClick={onCancel} className={quietButtonClass}>{tr('ui.settings.memory.cancel')}</button>
        <button type="submit" disabled={busy || !text.trim()} className={buttonClass}>{busy ? tr('ui.settings.memory.reading') : tr('ui.settings.memory.import')}</button>
      </div>
    </form>
  );
}

function kindLabel(kind: string) {
  switch (kind) {
    case 'about': return tr('ui.settings.memory.aboutYou');
    case 'preference': return 'PREFERENCE';
    case 'person': return 'PERSON';
    case 'project': return 'PROJECT';
    case 'howto': return 'HOW-TO';
    default: return 'OTHER';
  }
}

function scopeLabel(m: SavedMemory) {
  return m.scope === 'global' ? tr('ui.settings.memory.allAgents_8ed4') : (m.agent_name || 'ONE AGENT').toUpperCase();
}

function origin(m: SavedMemory) {
  const when = timeAgo(m.updated || m.created);
  if (m.source === 'user') return <>{tr('ui.settings.memory.youAddedThisWhen', { when })}</>;
  if (m.source === 'dream') return <>{tr('ui.settings.memory.addedByDreamModeWhen', { when })}</>;
  if (m.source_session_id) {
    return <>{tr('ui.settings.memory.savedIn')}{' '}<Link to={`/home?chat=${encodeURIComponent(m.source_session_id)}`} className="hover:text-fg hover:underline">{tr('ui.settings.memory.aChat')}</Link> · {when}</>;
  }
  return <>{tr('ui.settings.memory.savedByAnAgentWhen', { when })}</>;
}

function MemoryRow({ memory, agents, busy, highlighted, maxChars, onSave, onPin, onDelete }: {
  memory: SavedMemory;
  agents: AgentLite[];
  busy: boolean;
  highlighted: boolean;
  maxChars: number;
  onSave: (content: string, scope: MemoryScope, agentId: string | null) => Promise<boolean>;
  onPin: () => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <li className="p-2">
        <MemoryForm agents={agents} busy={busy} maxChars={maxChars} initial={memory}
          onCancel={() => setEditing(false)}
          onSave={(content, scope, agentId) => void onSave(content, scope, agentId).then((ok) => { if (ok) setEditing(false); })} />
      </li>
    );
  }
  return (
    <li className={cn('flex flex-wrap items-start gap-x-3 gap-y-1.5 px-3.5 py-3', highlighted && 'bg-accent/10')}>
      <div className="min-w-0 flex-1 basis-[240px]">
        <div className="text-[13.5px] leading-snug">{memory.content}</div>
        <div className="mt-1 text-[11.5px] text-fg-dim">
          {memory.pinned && <span className="text-accent-ink">{tr('ui.settings.memory.pinned')}{' '}</span>}
          {memory.expired
            ? <span className="text-danger-ink">{tr('ui.settings.memory.noLongerTrueEndedValid', { valid_until: memory.valid_until })}</span>
            : memory.valid_until ? <span>{tr('ui.settings.memory.trueUntilValidUntil', { valid_until: memory.valid_until })}</span> : null}
          {origin(memory)}
        </div>
      </div>
      {memory.kind && (
        <span className="mt-0.5 rounded bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] tracking-[0.06em] text-fg-dim">
          {kindLabel(memory.kind)}
        </span>
      )}
      <span className={cn('mt-0.5 rounded px-1.5 py-0.5 font-mono text-[10px] tracking-[0.06em]',
        memory.scope === 'global' ? 'bg-accent/15 text-accent-ink' : 'bg-panel-2 text-fg-dim')}>
        {scopeLabel(memory)}
      </span>
      <div className="flex items-center gap-0.5">
        <button type="button" onClick={onPin} disabled={busy} className={quietButtonClass} aria-pressed={memory.pinned}
          title={tr('ui.settings.memory.pinnedMemoriesAreAlwaysIn')}>
          {memory.pinned ? tr('ui.settings.memory.unpin') : tr('ui.settings.memory.pin')}
        </button>
        <button type="button" onClick={() => setEditing(true)} className={quietButtonClass}>{tr('ui.settings.memory.edit')}</button>
        <button type="button" onClick={onDelete} disabled={busy} className={cn(quietButtonClass, 'hover:text-danger-ink')}>{tr('ui.settings.memory.delete')}</button>
      </div>
    </li>
  );
}

function MemoryForm({ agents, busy, maxChars, initial, onSave, onCancel }: {
  agents: AgentLite[];
  busy: boolean;
  maxChars: number;
  initial?: SavedMemory;
  onSave: (content: string, scope: MemoryScope, agentId: string | null) => void;
  onCancel: () => void;
}) {
  const [content, setContent] = useState(initial?.content ?? '');
  const [target, setTarget] = useState(initial?.scope === 'agent' && initial.agent_id ? initial.agent_id : 'global');
  const scope: MemoryScope = target === 'global' ? 'global' : 'agent';
  const tooLong = content.length > maxChars;
  return (
    <form className="space-y-2.5 rounded-[10px] border border-line bg-bg p-3"
      aria-label={initial ? tr('ui.settings.memory.editMemory') : tr('ui.settings.memory.addMemory')}
      onSubmit={(e) => { e.preventDefault(); if (content.trim() && !tooLong) onSave(content.trim(), scope, scope === 'agent' ? target : null); }}>
      <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={2} autoFocus
        placeholder={tr('ui.settings.memory.eGIPreferShort')}
        aria-label={tr('ui.settings.memory.memory')}
        className="w-full resize-y rounded-lg border border-line bg-panel p-2.5 text-[13.5px] text-fg placeholder:text-fg-dim focus:border-accent focus:outline-none" />
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-[12.5px] text-fg-dim">
          {tr('ui.settings.memory.for')}
          <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label={tr('ui.settings.memory.whoRemembersThis')}
            className="h-9 rounded-lg border border-line bg-panel px-2 text-[12.5px] text-fg md:h-8">
            <option value="global">{tr('ui.settings.memory.allAgents')}</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{tr('ui.settings.memory.onlyName', { name: a.name })}</option>)}
          </select>
        </label>
        <span className={cn('text-[11.5px]', tooLong ? 'text-danger-ink' : 'text-fg-dim')}>{content.length}/{maxChars}</span>
        <span className="flex-1" />
        <button type="button" onClick={onCancel} className={quietButtonClass}>{tr('ui.settings.memory.cancel')}</button>
        <button type="submit" disabled={busy || !content.trim() || tooLong} className={buttonClass}>{tr('ui.settings.memory.save')}</button>
      </div>
    </form>
  );
}
