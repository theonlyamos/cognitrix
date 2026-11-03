import { api } from '@/lib/api';

export type MemoryScope = 'global' | 'agent';
export type AutoSave = 'auto' | 'explicit';

/** One thing your agents remember about you. */
export interface SavedMemory {
  id: string;
  content: string;
  scope: MemoryScope;
  agent_id: string | null;
  agent_name: string | null;
  source: 'user' | 'agent' | 'import';
  /** The chat it was saved in, if any. */
  source_session_id: string | null;
  pinned: boolean;
  /** UTC 'YYYY-MM-DD HH:MM:SS'. */
  created: string;
  updated: string;
}

export interface MemorySettings {
  enabled: boolean;
  history_enabled: boolean;
  auto_save: AutoSave;
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

export async function addMemory(input: { content: string; scope: MemoryScope; agent_id?: string | null; pinned?: boolean }): Promise<SavedMemory> {
  return (await api.post<SavedMemory>('/memories', input)).data;
}

export async function updateMemory(
  id: string,
  input: Partial<{ content: string; pinned: boolean; scope: MemoryScope; agent_id: string | null }>,
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

export interface MemoryChange {
  kind: 'saved' | 'updated' | 'forgotten';
  id: string;
  content: string;
  scope?: MemoryScope;
}

/** A Memory tool result, e.g. "Memory saved (id abc, global): Prefers short answers." */
export function memoryChange(toolName: string, result?: string | null): MemoryChange | null {
  if (!result || toolName.replace(/_/g, ' ').trim().toLowerCase() !== 'memory') return null;
  const m = /^Memory (saved|updated|forgotten) \(id ([^,)\s]+)(?:, (global|agent))?\): ([\s\S]+)$/.exec(result.trim());
  if (!m) return null;
  return { kind: m[1] as MemoryChange['kind'], id: m[2], scope: m[3] as MemoryScope | undefined, content: m[4].trim() };
}
