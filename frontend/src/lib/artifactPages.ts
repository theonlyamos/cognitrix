import { API_BASE, api } from '@/lib/api';

// Artifacts: pages agents publish. See cognitrix/artifact_pages on the server.

export type PageRole = 'owner' | 'edit' | 'view';
export type PageAccess = 'private' | 'people' | 'workspace' | 'public';
export type PagePower = 'ask' | 'data' | 'download' | 'apps';

export interface PageSummary {
  id: string;
  title: string;
  format: 'html' | 'markdown';
  version: number;
  viewer_version: number;
  size_bytes: number;
  role: PageRole;
  owner: { id: string; name: string };
  agent: { id: string; name: string } | null;
  session_id: string;
  task_id: string;
  live: boolean;
  access: PageAccess;
  pinned: boolean;
  powers: PagePower[];
  open_comments: number;
  created: string;
  updated: string;
}

export interface PageTask { id: string; title: string; schedule: string; next_run_at: string | null }
export interface PageVersion { number: number; note: string; created: string; size_bytes: number; by_task: boolean }

export interface PageDetail extends PageSummary {
  frame_url: string;
  versions: PageVersion[];
  task: PageTask | null;
  allowed_powers: PagePower[];
  consent_asked: boolean;
  share_version: number;
  public_url: string;
}

export interface ImageItem {
  id: string; kind: 'image'; title: string; mime_type: string; session_id: string;
  agent: { id: string; name: string } | null; width: number | null; height: number | null;
  size_bytes: number; created: string; updated: string;
}
export interface FileItem {
  id: string; kind: 'file'; title: string; mime_type: string; path: string; session_id: string; task_id: string;
  agent: { id: string; name: string } | null; size_bytes: number; created: string; updated: string;
}
export interface ArtifactList { pages: PageSummary[]; images: ImageItem[]; files: FileItem[]; public_links: boolean }

export interface CommentAnchor { x?: number; y?: number; text?: string }
export interface CommentEntry {
  id: string; author_id: string; author_name: string; is_agent: boolean; body: string;
  created: string; version: number; sent_to_agent: boolean;
}
export interface CommentThread extends CommentEntry { anchor: CommentAnchor; resolved: boolean; replies: CommentEntry[] }

export interface SharePerson { id: string; name: string; email: string }
export interface Sharing {
  people: (SharePerson & { role: 'view' | 'edit' })[];
  everyone: 'view' | 'edit' | '';
  public: boolean;
  public_url: string;
  public_links: boolean;
  share_version: number;
  version: number;
  directory: SharePerson[];
}

/** Where the server is, for addresses that go in an iframe or get copied. */
export const serverOrigin = (): string => {
  const base = API_BASE.replace(/\/api\/v1$/, '');
  return base || window.location.origin;
};

/** A frame address the API returned ("/api/v1/artifact-frame/...") made absolute. */
export const frameSrc = (path: string): string => `${serverOrigin()}${path}`;

export const pageLink = (id: string): string => `${window.location.origin}/artifacts/${id}`;
export const publicLink = (path: string): string => `${window.location.origin}${path}`;

const OPEN_EVENT = 'cognitrix:open-artifact';
const UPDATED_EVENT = 'cognitrix:artifact-updated';

/** Ask the page on screen to open an artifact in its side panel. Returns false
 *  when nothing took it (the caller then goes to /artifacts/:id). */
export function requestOpenArtifact(id: string): boolean {
  const event = new CustomEvent(OPEN_EVENT, { detail: { id }, cancelable: true });
  return !window.dispatchEvent(event);
}

export function onOpenArtifact(handler: (id: string) => void): () => void {
  const listener = (event: Event) => {
    const id = (event as CustomEvent<{ id?: string }>).detail?.id;
    if (!id) return;
    event.preventDefault();
    handler(id);
  };
  window.addEventListener(OPEN_EVENT, listener);
  return () => window.removeEventListener(OPEN_EVENT, listener);
}

/** An agent just published: viewers showing that artifact reload it now. */
export function announceArtifactUpdate(id: string): void {
  window.dispatchEvent(new CustomEvent(UPDATED_EVENT, { detail: { id } }));
}

export function onArtifactUpdate(handler: (id: string) => void): () => void {
  const listener = (event: Event) => {
    const id = (event as CustomEvent<{ id?: string }>).detail?.id;
    if (id) handler(id);
  };
  window.addEventListener(UPDATED_EVENT, listener);
  return () => window.removeEventListener(UPDATED_EVENT, listener);
}

/** What a Publish Artifact tool result says it published, or null. */
export function publishedFrom(toolName: string, result: string | undefined): { id: string; title: string; version: number } | null {
  if (toolName.toLowerCase().replace(/_/g, ' ').trim() !== 'publish artifact' || !result) return null;
  const match = /"(.{1,160}?)" \(artifact_id ([A-Za-z0-9_-]{1,64}), version (\d+)\)/.exec(result);
  return match ? { title: match[1], id: match[2], version: Number(match[3]) } : null;
}

/** Server stamps are UTC "YYYY-MM-DD HH:MM:SS[.ffffff]" (or ISO). */
export function parseStamp(stamp: string | null | undefined): Date | null {
  if (!stamp) return null;
  const iso = stamp.includes('T') ? stamp : `${stamp.replace(' ', 'T')}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function ago(stamp: string | null | undefined, now = Date.now()): string {
  const date = parseStamp(stamp);
  if (!date) return '';
  const seconds = Math.max(0, Math.round((now - date.getTime()) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days < 30 ? `${days} d ago` : date.toLocaleDateString();
}

export function until(stamp: string | null | undefined, now = Date.now()): string {
  const date = parseStamp(stamp);
  if (!date) return '';
  const minutes = Math.round((date.getTime() - now) / 60000);
  if (minutes <= 0) return 'any moment';
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `in ${hours} h` : `in ${Math.round(hours / 24)} d`;
}

export const sizeLabel = (bytes: number): string =>
  bytes >= 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

export const POWER_COPY: Record<PagePower, { title: string; detail: string; tag: string }> = {
  apps: { title: 'Your connected apps', detail: 'Read from apps you connected, as you. It can only read, never send or change, and never sees your sign-in.', tag: 'read' },
  ask: { title: 'Ask your agent', detail: 'Buttons on the page can send a question to an agent. Answers use your model key.', tag: 'ask' },
  data: { title: 'Save page data', detail: 'Keeps small things you do on the page, such as ticked boxes. Some may be shared with other viewers.', tag: 'store' },
  download: { title: 'Download files', detail: 'The page can hand you a file it made, such as a CSV export.', tag: 'file' },
};

export async function bridgeCall(pageId: string, kind: string, payload: unknown): Promise<unknown> {
  const { data } = await api.post<{ value: unknown }>(`/artifact-pages/${pageId}/bridge`, { kind, payload });
  return data.value;
}
