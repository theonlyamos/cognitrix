import { useEffect, useMemo, useState } from 'react';
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

  const load = () => listMemories().then(setData).catch((e) => setLoadError(errorMessage(e, 'Could not load memories.')));

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
  if (!data) return <p className="text-[13px] text-fg-dim">Loading…</p>;

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
    if (r.duplicates) parts.push(`${r.duplicates} already saved.`);
    if (r.skipped.length) parts.push(`${r.skipped.length} left out (secrets, too long, or memory full).`);
    if (!r.proposed && !r.duplicates) parts.splice(0, 1, `Nothing worth keeping was found in ${what}.`);
    setNotice(parts.join(' '));
  };
  const chips = [
    { id: 'all', label: 'All' },
    { id: 'global', label: 'Global' },
    ...agents.filter((a) => agentIds.has(a.id)).map((a) => ({ id: a.id, label: a.name })),
  ];

  return (
    <div className="space-y-2">
      <Group>
        <SettingRow label="Use saved memories" detail="Agents read the notes below in every chat and can add new ones.">
          <Toggle label="Use saved memories" checked={settings.enabled} disabled={busy}
            onChange={(enabled) => void run(() => saveMemorySettings({ enabled }), 'Could not change the setting.')} />
        </SettingRow>
        <SettingRow label="Reference chat history" detail="Agents can search and read your past chats when you mention earlier work. Incognito chats are never searched.">
          <Toggle label="Reference chat history" checked={settings.history_enabled} disabled={busy}
            onChange={(history_enabled) => void run(() => saveMemorySettings({ history_enabled }), 'Could not change the setting.')} />
        </SettingRow>
        <SettingRow
          label="Save new memories"
          detail={settings.auto_save === 'auto'
            ? 'Agents save what seems worth keeping. A note shows in the chat each time, with Undo. After reading web pages, files or app content, they ask first.'
            : settings.auto_save === 'ask'
              ? 'Agents propose memories and you approve each one in the chat (Save or No).'
              : 'Agents only save something when you ask them to remember it.'}
        >
          <Segmented
            label="Save new memories"
            value={settings.auto_save}
            options={[{ value: 'auto', label: 'Automatically' }, { value: 'ask', label: 'Ask me first' }, { value: 'explicit', label: 'When I ask' }]}
            onChange={(auto_save) => void run(() => saveMemorySettings({ auto_save }), 'Could not change the setting.')}
          />
        </SettingRow>
      </Group>

      {notice && (
        <p role="status" className="!mt-4 rounded-lg border border-line bg-panel px-3 py-2 text-[12.5px] text-fg-dim">
          {notice} <button type="button" className="ml-1 text-accent-ink hover:underline" onClick={() => setNotice(null)}>Dismiss</button>
        </p>
      )}

      {pending.length > 0 && (
        <section aria-label="Memories waiting for your OK">
          <div className="!mt-6 flex flex-wrap items-center gap-2">
            <SubHeading className="!mt-0 flex-1">{`WAITING FOR YOUR OK · ${pending.length}`}</SubHeading>
            <button type="button" disabled={busy} className={quietButtonClass}
              onClick={() => void run(() => settlePendingMemories(false), 'Could not discard them.')}>Discard all</button>
            <button type="button" disabled={busy} className={buttonClass}
              onClick={() => void run(() => settlePendingMemories(true), 'Could not save them.')}>Save all</button>
          </div>
          <p className="mb-2 mt-1 text-[12px] text-fg-dim">Agents don’t see these until you save them.</p>
          <ul className="divide-y divide-line rounded-[12px] border border-accent/40 bg-panel">
            {pending.map((m) => (
              <PendingRow key={m.id} memory={m} busy={busy} highlighted={m.id === highlight}
                replaced={m.replaces ? saved.find((x) => x.id === m.replaces)?.content : undefined}
                onApprove={() => void run(() => approveMemory(m.id), 'Could not save the memory.')}
                onDiscard={() => void run(() => deleteMemory(m.id), 'Could not discard the memory.')} />
            ))}
          </ul>
        </section>
      )}

      <SubHeading className="!mt-6">{`SAVED MEMORIES · ${saved.length}`}</SubHeading>

      <div className={cn('space-y-3', !settings.enabled && 'opacity-60')}>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex h-9 min-w-[200px] flex-1 items-center gap-2 rounded-lg border border-line bg-bg px-2.5 text-fg-dim focus-within:border-accent">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
            </svg>
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search memories"
              aria-label="Search memories" className="w-full bg-transparent text-[13px] text-fg outline-none placeholder:text-fg-dim" />
          </label>
          <button type="button" onClick={() => { setError(null); setAdding(true); }} className={primaryButtonClass}>+ Add</button>
        </div>
        {chips.length > 2 && (
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Show memories for">
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
            }, 'Could not save the memory.')} />
        )}

        {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}

        {importing && (
          <ImportForm busy={busy} onCancel={() => setImporting(false)}
            onImport={(text) => void run(async () => {
              const result = await importMemories(text);
              setImporting(false);
              proposedNotice(result, 'your import');
            }, 'Could not import those memories.')} />
        )}

        {saved.length === 0 ? (
          <p className="rounded-[10px] border border-line bg-panel px-3.5 py-4 text-[13px] leading-relaxed text-fg-dim">
            Nothing saved yet. Tell an agent “remember that…” in any chat, add a memory here, or import what another assistant remembers.
          </p>
        ) : shown.length === 0 ? (
          <p className="px-1 text-[13px] text-fg-dim">No memories match.</p>
        ) : (
          <ul className="divide-y divide-line rounded-[12px] border border-line bg-panel">
            {shown.map((m) => (
              <MemoryRow key={m.id} memory={m} agents={agents} busy={busy} highlighted={m.id === highlight} maxChars={data.max_chars}
                onSave={(content, scope, agentId) => run(() => updateMemory(m.id, { content, scope, agent_id: agentId }), 'Could not save the memory.')}
                onPin={() => void run(() => updateMemory(m.id, { pinned: !m.pinned }), 'Could not pin the memory.')}
                onDelete={() => void run(() => deleteMemory(m.id), 'Could not delete the memory.')} />
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-3 text-[12.5px]">
        <button type="button" className="text-fg-dim hover:text-fg hover:underline" onClick={() => { setError(null); setImporting(true); }}>
          Import…
        </button>
        {saved.length > 0 && (
          <>
            <button type="button" className="text-fg-dim hover:text-fg hover:underline"
              onClick={() => void exportMemories('markdown').catch((e) => setError(errorMessage(e, 'Could not export.')))}>Export Markdown</button>
            <button type="button" className="text-fg-dim hover:text-fg hover:underline"
              onClick={() => void exportMemories('json').catch((e) => setError(errorMessage(e, 'Could not export.')))}>Export JSON</button>
          </>
        )}
        {legacy > 0 && (
          <button type="button" disabled={busy} className="text-fg-dim hover:text-fg hover:underline disabled:opacity-60"
            title="A model reads the old background memory and proposes facts for you to approve."
            onClick={() => void run(async () => proposedNotice(await convertLegacyMemory(), 'your old automatic memory'), 'Could not convert the old memory.')}>
            {busy ? 'Working…' : 'Turn old memory into saved memories'}
          </button>
        )}
        {legacy > 0 && (
          <button type="button" disabled={busy} className="text-fg-dim hover:text-fg hover:underline disabled:opacity-60"
            onClick={() => void run(async () => { await deleteLegacyMemory(); setLegacy(0); }, 'Could not delete the old memory.')}
            title="Before saved memories, agents kept copies of chat messages in the background. They're no longer used.">
            {`Delete old automatic memory (${legacy} messages)`}
          </button>
        )}
        <span className="flex-1" />
        {data.memories.length > 0 && (confirmAll ? (
          <span className="flex items-center gap-2">
            <span className="text-danger-ink">Delete all {data.memories.length} memories?</span>
            <button type="button" disabled={busy} className="font-medium text-danger-ink hover:underline"
              onClick={() => void run(async () => { await deleteAllMemories(); setConfirmAll(false); }, 'Could not delete memories.')}>
              Delete all
            </button>
            <button type="button" className="text-fg-dim hover:text-fg" onClick={() => setConfirmAll(false)}>Cancel</button>
          </span>
        ) : (
          <button type="button" className="text-danger-ink hover:underline" onClick={() => setConfirmAll(true)}>Delete all memories</button>
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
          {replaced && <>Replaces “{replaced}” · </>}
          {memory.source === 'import' ? <>Imported · {timeAgo(memory.created)}</> : origin(memory)}
        </div>
      </div>
      <span className={cn('mt-0.5 rounded px-1.5 py-0.5 font-mono text-[10px] tracking-[0.06em]',
        memory.scope === 'global' ? 'bg-accent/15 text-accent-ink' : 'bg-panel-2 text-fg-dim')}>
        {scopeLabel(memory)}
      </span>
      <div className="flex items-center gap-0.5">
        <button type="button" onClick={onDiscard} disabled={busy} className={cn(quietButtonClass, 'hover:text-danger-ink')}>Discard</button>
        <button type="button" onClick={onApprove} disabled={busy} className={buttonClass}>Save</button>
      </div>
    </li>
  );
}

function ImportForm({ busy, onImport, onCancel }: { busy: boolean; onImport: (text: string) => void; onCancel: () => void }) {
  const [text, setText] = useState('');
  return (
    <form className="space-y-2.5 rounded-[10px] border border-line bg-bg p-3" aria-label="Import memories"
      onSubmit={(e) => { e.preventDefault(); if (text.trim()) onImport(text.trim()); }}>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        Paste what another assistant remembers about you (in ChatGPT: Settings › Personalization › Memory; in Claude: ask
        “list everything you remember about me”), your own notes, or a Cognitrix export. Each fact waits for your OK before agents see it.
      </p>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} autoFocus aria-label="Memories to import"
        placeholder={'- Prefers short answers\n- Works on a Python and React app\n- Lives in Accra'}
        className="w-full resize-y rounded-lg border border-line bg-panel p-2.5 text-[13px] text-fg placeholder:text-fg-dim focus:border-accent focus:outline-none" />
      <div className="flex items-center gap-2">
        <span className="flex-1 text-[11.5px] text-fg-dim">Your main agent’s model splits it into separate facts.</span>
        <button type="button" onClick={onCancel} className={quietButtonClass}>Cancel</button>
        <button type="submit" disabled={busy || !text.trim()} className={buttonClass}>{busy ? 'Reading…' : 'Import'}</button>
      </div>
    </form>
  );
}

function scopeLabel(m: SavedMemory) {
  return m.scope === 'global' ? 'GLOBAL' : (m.agent_name || 'ONE AGENT').toUpperCase();
}

function origin(m: SavedMemory) {
  const when = timeAgo(m.updated || m.created);
  if (m.source === 'user') return <>You added this · {when}</>;
  if (m.source_session_id) {
    return <>Saved in <Link to={`/home?chat=${encodeURIComponent(m.source_session_id)}`} className="hover:text-fg hover:underline">a chat</Link> · {when}</>;
  }
  return <>Saved by an agent · {when}</>;
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
          {memory.pinned && <span className="text-accent-ink">Pinned · </span>}
          {origin(memory)}
        </div>
      </div>
      <span className={cn('mt-0.5 rounded px-1.5 py-0.5 font-mono text-[10px] tracking-[0.06em]',
        memory.scope === 'global' ? 'bg-accent/15 text-accent-ink' : 'bg-panel-2 text-fg-dim')}>
        {scopeLabel(memory)}
      </span>
      <div className="flex items-center gap-0.5">
        <button type="button" onClick={onPin} disabled={busy} className={quietButtonClass} aria-pressed={memory.pinned}
          title="Pinned memories are always in the agent's instructions">
          {memory.pinned ? 'Unpin' : 'Pin'}
        </button>
        <button type="button" onClick={() => setEditing(true)} className={quietButtonClass}>Edit</button>
        <button type="button" onClick={onDelete} disabled={busy} className={cn(quietButtonClass, 'hover:text-danger-ink')}>Delete</button>
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
      aria-label={initial ? 'Edit memory' : 'Add memory'}
      onSubmit={(e) => { e.preventDefault(); if (content.trim() && !tooLong) onSave(content.trim(), scope, scope === 'agent' ? target : null); }}>
      <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={2} autoFocus
        placeholder="e.g. I prefer short answers with code first."
        aria-label="Memory"
        className="w-full resize-y rounded-lg border border-line bg-panel p-2.5 text-[13.5px] text-fg placeholder:text-fg-dim focus:border-accent focus:outline-none" />
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-[12.5px] text-fg-dim">
          For
          <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Who remembers this"
            className="h-9 rounded-lg border border-line bg-panel px-2 text-[12.5px] text-fg md:h-8">
            <option value="global">All agents</option>
            {agents.map((a) => <option key={a.id} value={a.id}>Only {a.name}</option>)}
          </select>
        </label>
        <span className={cn('text-[11.5px]', tooLong ? 'text-danger-ink' : 'text-fg-dim')}>{content.length}/{maxChars}</span>
        <span className="flex-1" />
        <button type="button" onClick={onCancel} className={quietButtonClass}>Cancel</button>
        <button type="submit" disabled={busy || !content.trim() || tooLong} className={buttonClass}>Save</button>
      </div>
    </form>
  );
}
