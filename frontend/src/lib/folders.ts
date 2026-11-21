import { api } from '@/lib/api';
import { invokeDesktop, isDesktop } from '@/lib/desktop';

/** A folder an agent may work in. Mirrors cognitrix/common/folders.py. */
export interface WorkFolder {
  path: string;
  access: 'read' | 'write';
  name?: string;
  primary?: boolean;
}

export interface FolderInfo {
  desktop: boolean;
  /** null: anywhere on this machine except protected places. */
  allowed_roots: string[] | null;
  /** The app's working folder: what agents use when none are set. */
  working_folder: string;
}

export const folderName = (f: WorkFolder) =>
  f.name || f.path.split(/[\\/]/).filter(Boolean).pop() || f.path;

export async function folderInfo(): Promise<FolderInfo> {
  return (await api.get<FolderInfo>('/folders/info')).data;
}

/** Ask the server to check a folder; resolves to its stored shape or throws. */
export async function checkFolder(path: string, access: WorkFolder['access']): Promise<WorkFolder> {
  return (await api.post<WorkFolder>('/folders/check', { path, access })).data;
}

/** The AGENTS.md files a chat in this folder follows (its own and its
 *  parents' up to the repository root) and how many subfolders have one. */
export interface FolderInstructions {
  files: string[];
  nested: number;
}

const instructionsCache = new Map<string, Promise<FolderInstructions>>();

export function folderInstructions(path: string): Promise<FolderInstructions> {
  let hit = instructionsCache.get(path);
  if (!hit) {
    hit = api.get<FolderInstructions>('/folders/instructions', { params: { path } })
      .then((r) => r.data)
      .catch(() => ({ files: [], nested: 0 }));
    instructionsCache.set(path, hit);
    // Re-check after a minute: someone may add or change an AGENTS.md.
    setTimeout(() => instructionsCache.delete(path), 60_000);
  }
  return hit;
}

export async function getChatFolders(sessionId: string): Promise<WorkFolder[] | null> {
  return (await api.get<{ folders: WorkFolder[] | null }>(`/folders/sessions/${sessionId}`)).data.folders;
}

export async function setChatFolders(sessionId: string, folders: WorkFolder[]): Promise<WorkFolder[]> {
  return (await api.put<{ folders: WorkFolder[] }>(`/folders/sessions/${sessionId}`, { folders })).data.folders;
}

/** Native folder picker in the desktop app; null in a browser or when cancelled. */
export async function pickFolder(): Promise<string | null> {
  if (!isDesktop()) return null;
  return invokeDesktop<string | null>('pick_folder', { title: 'Add a folder for the agent' });
}

const RECENT_KEY = 'recentFolders';

export function recentFolders(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((p) => typeof p === 'string').slice(0, 6) : [];
  } catch {
    return [];
  }
}

export function rememberFolder(path: string) {
  try {
    const next = [path, ...recentFolders().filter((p) => p !== path)].slice(0, 6);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: recents are a convenience */
  }
}

/** Keep exactly one primary folder, first in the list. */
export function withPrimary(folders: WorkFolder[], primaryPath?: string): WorkFolder[] {
  if (!folders.length) return folders;
  const target = primaryPath ?? (folders.find((f) => f.primary) || folders[0]).path;
  const marked = folders.map((f) => ({ ...f, primary: f.path === target }));
  return [...marked.filter((f) => f.primary), ...marked.filter((f) => !f.primary)];
}

/** One folder in the server folder browser. */
export interface BrowseEntry {
  name: string;
  path: string;
  writable: boolean;
}

export interface BrowseListing {
  /** null: the starting folders are listed. */
  path: string | null;
  /** null: can't go further up. */
  parent: string | null;
  entries: BrowseEntry[];
  truncated: boolean;
  writable?: boolean;
}

/** Subfolders of a folder on the machine running Cognitrix (web app browser). */
export async function browseFolders(path?: string | null, hidden = false): Promise<BrowseListing> {
  return (await api.get<BrowseListing>('/folders/browse', { params: { path: path || '', hidden } })).data;
}

export async function createFolder(parent: string, name: string): Promise<BrowseEntry> {
  return (await api.post<BrowseEntry>('/folders/create', { parent, name })).data;
}
