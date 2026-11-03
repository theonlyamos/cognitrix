import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { Toggle } from '@/pages/Notifications';
import { timeAgo } from '@/lib/agentDraft';
import { cn } from '@/lib/utils';
import {
  addMemory, deleteAllMemories, deleteLegacyMemory, deleteMemory, legacyMemoryCount, listMemories,
  saveMemorySettings, updateMemory, type MemoryList, type MemoryScope, type SavedMemory,
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

  const load = () => listMemories().then(setData).catch((e) => setLoadError(errorMessage(e, 'Could not load memories.')));

  useEffect(() => {
    void load();
    api.get<AgentLite[]>('/agents').then((r) => setAgents((r.data || []).map((a) => ({ id: a.id, name: a.name })))).catch(() => {});
    legacyMemoryCount().then(setLegacy).catch(() => {});
  }, []);

  const shown = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.memories.filter((m) => (
      (filter === 'all' || (filter === 'global' ? m.scope === 'global' : m.agent_id === filter))
      && (!q || m.content.toLowerCase().includes(q))
    ));
  }, [data, filter, query]);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!data) return <p className="text-[13px] text-fg-dim">Loading…</p>;

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(errorMessage(e, fallback));
    } finally {
      setBusy(false);
    }
  };

  const settings = data.settings;
  const agentIds = new Set(data.memories.filter((m) => m.agent_id).map((m) => m.agent_id as string));
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
            ? 'Agents save what seems worth keeping. A note shows in the chat each time, with Undo.'
            : 'Agents only save something when you ask them to remember it.'}
        >
          <Segmented
            label="Save new memories"
            value={settings.auto_save}
            options={[{ value: 'auto', label: 'Automatically' }, { value: 'explicit', label: 'When I ask' }]}
            onChange={(auto_save) => void run(() => saveMemorySettings({ auto_save }), 'Could not change the setting.')}
          />
        </SettingRow>
      </Group>

      <SubHeading className="!mt-6">{`SAVED MEMORIES · ${data.memories.length}`}</SubHeading>

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

        {data.memories.length === 0 ? (
          <p className="rounded-[10px] border border-line bg-panel px-3.5 py-4 text-[13px] leading-relaxed text-fg-dim">
            Nothing saved yet. Tell an agent “remember that…” in any chat, or add a memory here.
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
  onSave: (content: string, scope: MemoryScope, agentId: string | null) => Promise<void>;
  onPin: () => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <li className="p-2">
        <MemoryForm agents={agents} busy={busy} maxChars={maxChars} initial={memory}
          onCancel={() => setEditing(false)}
          onSave={(content, scope, agentId) => void onSave(content, scope, agentId).then(() => setEditing(false))} />
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
