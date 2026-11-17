import { api } from '@/lib/api';

export type MemoryScope = 'global' | 'agent';
export type AutoSave = 'auto' | 'ask' | 'explicit';

/** One thing your agents remember about you. */
export interface SavedMemory {
  id: string;
  content: string;
  scope: MemoryScope;
  agent_id: string | null;
  agent_name: string | null;
  source: 'user' | 'agent' | 'import' | 'dream';
  /** The chat it was saved in, if any. */
  source_session_id: string | null;
  pinned: boolean;
  /** Proposed, waiting for your OK; agents don't see it yet. */
  pending?: boolean;
  /** For a proposed change: the memory it replaces once approved. */
  replaces?: string | null;
  /** UTC 'YYYY-MM-DD HH:MM:SS'. */
  created: string;
  updated: string;
  /** What it's about: about, preference, person, project, howto or other. */
  kind?: MemoryKind | '';
  /** 'YYYY-MM-DD' after which it is no longer true; '' when it doesn't end. */
  valid_until?: string;
  /** Past its valid_until: agents no longer see it. */
  expired?: boolean;
  last_used?: string;
  use_count?: number;
}

export type MemoryKind = 'about' | 'preference' | 'person' | 'project' | 'howto' | 'other';

export interface MemorySettings {
  enabled: boolean;
  history_enabled: boolean;
  auto_save: AutoSave;
  /** Dream mode: tidy memory each night. */
  dream_enabled?: boolean;
}

export interface MemoryList {
  memories: SavedMemory[];
  settings: MemorySettings;
  limit: number;
  max_chars: number;
}

export async function listMemories(): Promise<MemoryList> {
  return (await api.get<MemoryList>('/memories')).data;
}

export async function addMemory(input: {
  content: string; scope: MemoryScope; agent_id?: string | null; pinned?: boolean; kind?: string; valid_until?: string;
}): Promise<SavedMemory> {
  return (await api.post<SavedMemory>('/memories', input)).data;
}

export async function updateMemory(
  id: string,
  input: Partial<{ content: string; pinned: boolean; scope: MemoryScope; agent_id: string | null; kind: string; valid_until: string }>,
): Promise<SavedMemory> {
  return (await api.patch<SavedMemory>(`/memories/${encodeURIComponent(id)}`, input)).data;
}

export async function deleteMemory(id: string): Promise<void> {
  await api.delete(`/memories/${encodeURIComponent(id)}`);
}

export async function deleteAllMemories(): Promise<number> {
  return (await api.post<{ deleted: number }>('/memories/delete-all', { confirm: true })).data.deleted;
}

export async function saveMemorySettings(input: Partial<MemorySettings>): Promise<MemorySettings> {
  return (await api.put<MemorySettings>('/memories/settings', input)).data;
}

export async function legacyMemoryCount(): Promise<number> {
  return (await api.get<{ count: number }>('/memories/legacy')).data.count;
}

export async function deleteLegacyMemory(): Promise<number> {
  return (await api.delete<{ deleted: number }>('/memories/legacy')).data.deleted;
}

export async function approveMemory(id: string): Promise<SavedMemory> {
  return (await api.post<SavedMemory>(`/memories/${encodeURIComponent(id)}/approve`)).data;
}

/** Approve (or discard) every memory waiting for your OK. */
export async function settlePendingMemories(approve: boolean): Promise<void> {
  await api.post('/memories/pending', { approve });
}

export interface ImportResult {
  proposed: number;
  duplicates: number;
  skipped: { content: string; reason: string }[];
  left_out?: number;
  read_by?: 'file' | 'model' | 'lines';
  messages_read?: number;
}

/** Turn a paste (another assistant's memory list, notes, or an export) into memories waiting for your OK. */
export async function importMemories(text: string): Promise<ImportResult> {
  return (await api.post<ImportResult>('/memories/import', { text })).data;
}

/** Read the old automatic memory into memories waiting for your OK. */
export async function convertLegacyMemory(): Promise<ImportResult> {
  return (await api.post<ImportResult>('/memories/legacy/convert')).data;
}

/** Download every memory as JSON or Markdown. */
export async function exportMemories(format: 'json' | 'markdown'): Promise<void> {
  const res = await api.get(`/memories/export?format=${format}`, { responseType: 'blob' });
  const blob = res.data instanceof Blob ? res.data : new Blob([typeof res.data === 'string' ? res.data : JSON.stringify(res.data, null, 2)]);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `cognitrix-memories-${new Date().toISOString().slice(0, 10)}.${format === 'json' ? 'json' : 'md'}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface MemoryChange {
  kind: 'saved' | 'updated' | 'forgotten' | 'proposed';
  id: string;
  content: string;
  scope?: MemoryScope;
}

/**
 * A Memory tool result, e.g. "Memory saved (id abc, global): Prefers short answers."
 * A proposal ("Memory proposed (…): …") carries a note for the agent on the next line.
 */
export function memoryChange(toolName: string, result?: string | null): MemoryChange | null {
  if (!result || toolName.replace(/_/g, ' ').trim().toLowerCase() !== 'memory') return null;
  const first = result.trim().split('\n')[0];
  const m = /^Memory (saved|updated|forgotten|proposed) \(id ([^,)\s]+)(?:, (global|agent))?\): ([\s\S]+)$/.exec(first);
  if (!m) return null;
  return { kind: m[1] as MemoryChange['kind'], id: m[2], scope: m[3] as MemoryScope | undefined, content: m[4].trim() };
}

// ---------------------------------------------------------------- history

export interface MemorySnapshot { id: string; content: string; scope: MemoryScope; kind?: string; valid_until?: string; pinned?: boolean }

/** One recorded change to a memory, with Undo. */
export interface MemoryHistoryEntry {
  id: string;
  memory_id: string;
  action: 'create' | 'update' | 'delete' | 'approve';
  /** Who made it: 'person', 'agent' or 'dream'. */
  by: string;
  dream_id: string | null;
  before: MemorySnapshot | null;
  after: MemorySnapshot | null;
  note: string;
  created: string;
  undone: boolean;
}

export async function memoryHistory(params: { memory_id?: string; dream_id?: string; limit?: number } = {}): Promise<MemoryHistoryEntry[]> {
  return (await api.get<{ changes: MemoryHistoryEntry[] }>('/memories/history', { params })).data.changes;
}

export async function undoMemoryChange(id: string): Promise<void> {
  await api.post(`/memories/history/${encodeURIComponent(id)}/undo`);
}

// ---------------------------------------------------------------- about you

export interface MemoryProfile {
  text: string;
  /** Written or locked by you; dream mode leaves it alone. */
  locked: boolean;
  updated: string | null;
  by_dream: boolean;
}

export async function getMemoryProfile(): Promise<MemoryProfile> {
  return (await api.get<MemoryProfile>('/memories/profile')).data;
}

export async function saveMemoryProfile(text: string, locked?: boolean): Promise<MemoryProfile> {
  return (await api.put<MemoryProfile>('/memories/profile', { text, locked })).data;
}

// ---------------------------------------------------------------- dream mode

export interface DreamRun {
  id: string;
  trigger: 'night' | 'manual' | string;
  status: 'running' | 'done' | 'skipped' | 'failed';
  started: string;
  finished: string | null;
  summary: string;
  stats: Record<string, number | boolean>;
  error?: string;
}

export interface DreamProposal {
  id: string;
  dream_id: string;
  action: 'merge' | 'update' | 'delete' | 'add' | 'conflict';
  /** The memories it touches; content is null when one was deleted since. */
  memories: { id: string; content: string | null; source: string | null }[];
  content: string | null;
  kind: string | null;
  valid_until: string | null;
  reason: string;
  status: string;
  created: string;
}

export interface DreamStatus {
  enabled: boolean;
  running: boolean;
  runs: DreamRun[];
  proposals: DreamProposal[];
  /** Local hour the nightly dream runs. */
  hour: number;
}

export async function dreamStatus(): Promise<DreamStatus> {
  return (await api.get<DreamStatus>('/memories/dream')).data;
}

/** Tidy now: run dream mode right away. */
export async function dreamNow(): Promise<DreamRun> {
  return (await api.post<DreamRun>('/memories/dream')).data;
}

export async function answerDreamProposal(id: string, approve: boolean, keepId?: string): Promise<void> {
  await api.post(`/memories/dream/proposals/${encodeURIComponent(id)}`, { approve, keep_id: keepId });
}

export async function undoDream(id: string): Promise<number> {
  return (await api.post<{ undone: number }>(`/memories/dream/${encodeURIComponent(id)}/undo`)).data.undone;
}
