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
  /** Chat review: learn from finished chats and tasks each night. */
  review_enabled?: boolean;
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

// ---------------------------------------------------------------- chat review

export interface ReviewRun {
  id: string;
  trigger: 'night' | 'manual' | string;
  status: 'running' | 'done' | 'skipped' | 'failed';
  started: string;
  finished: string | null;
  summary: string;
  /** Counts; while running also `at` (0-based item being read), `total` and `beat` (a heartbeat time). */
  stats: Record<string, number | string>;
  error?: string | null;
}

/** Something the chat review made or changed. */
export interface LearnedItem {
  id: string;
  run_id: string;
  kind: 'memory' | 'skill' | 'knowledge';
  /** 'updated': an existing skill or note was improved; 'proposed': a memory waiting for your OK. */
  action: 'created' | 'updated' | 'proposed';
  target_id: string | null;
  name: string | null;
  description: string | null;
  content: string;
  source_kind: 'chat' | 'task' | string;
  source_id: string | null;
  source_title: string;
  /** Where the chat or task opens in the app. */
  source_link: string | null;
  created: string;
  removed: boolean;
}

export interface ReviewStatus {
  enabled: boolean;
  running: boolean;
  runs: ReviewRun[];
  learned: LearnedItem[];
  /** Local hour the nightly review runs (with the tidy-up). */
  hour: number;
}

export async function reviewStatus(): Promise<ReviewStatus> {
  return (await api.get<ReviewStatus>('/memories/review')).data;
}

/** Review chats now: learn from finished chats and tasks right away. */
export async function reviewNow(): Promise<ReviewRun> {
  return (await api.post<ReviewRun>('/memories/review')).data;
}

/** Remove what the review made, or put an improved skill or note back as it was. */
export async function removeLearned(id: string): Promise<LearnedItem> {
  return (await api.post<LearnedItem>(`/memories/review/learned/${encodeURIComponent(id)}/remove`)).data;
}

// ---------------------------------------------------------------- Notion backup

export interface NotionBackupState {
  /** Back up after every tidy. */
  enabled: boolean;
  /** The person has a Notion connected app switched on. */
  connected: boolean;
  /** '' (never ran), 'ok', 'failed', 'not_connected', 'restore_first' or 'waiting_review' (restored notes wait for an OK). */
  status: '' | 'ok' | 'failed' | 'not_connected' | 'restore_first' | 'waiting_review';
  message: string;
  last_at: string | null;
  last_ok: string | null;
  page_url: string | null;
  /** Memories in the last backup that worked. */
  count: number;
  /** The skills backup (its own Notion page, same switch). */
  skills?: NotionSkillsBackupState;
}

export interface NotionSkillsBackupState {
  /** '' (never ran), 'ok', 'failed', 'not_connected' or 'restore_first' (Notion has skills this install lacks). */
  status: '' | 'ok' | 'failed' | 'not_connected' | 'restore_first';
  message: string;
  last_at: string | null;
  last_ok: string | null;
  page_url: string | null;
  /** Skills in the last backup that worked. */
  count: number;
}

export interface NotionRestoreResult {
  restored: number;
  already: number;
  /** Came back as proposals (their agent or project is gone). */
  waiting: number;
  left_out: number;
  about_you: boolean;
  saved: string;
  page_url: string | null;
  /** Skills: added, already here, kept (this install's own copy, or a shared skill, has the name), left out. */
  skills?: { restored: number; already: number; kept: number; left_out: number };
  state: NotionBackupState;
}

export async function notionBackupStatus(): Promise<NotionBackupState> {
  return (await api.get<NotionBackupState>('/memories/notion')).data;
}

export async function setNotionBackup(enabled: boolean): Promise<NotionBackupState> {
  return (await api.put<NotionBackupState>('/memories/notion', { enabled })).data;
}

/** Back up now: replaces the Notion page right away. */
export async function notionBackupNow(): Promise<NotionBackupState> {
  return (await api.post<NotionBackupState>('/memories/notion/backup')).data;
}

export async function restoreFromNotion(): Promise<NotionRestoreResult> {
  return (await api.post<NotionRestoreResult>('/memories/notion/restore')).data;
}
