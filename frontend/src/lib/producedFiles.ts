import { api } from '@/lib/api';
import { serverOrigin } from '@/lib/artifactPages';
import { tr, trn } from '@/lib/i18n';

// Files agents make: shared with Share File, or found at the end of a turn.
// See cognitrix/produced_files on the server.

export type FileKind =
  | 'image' | 'video' | 'audio' | 'pdf' | 'doc' | 'slides' | 'sheet' | 'text' | 'html' | 'data'
  | 'archive' | 'model' | 'font' | 'code' | 'other';

export type FileSource = 'server' | 'sandbox' | 'pc';

export interface FileInfo {
  width?: number;
  height?: number;
  duration?: number;
  pages?: number;
  rows?: number;
  sheets?: number;
  preview?: string[][];
  peaks?: number[];
  count?: number;
  unpacked_size?: number;
  first?: string[];
  slides?: number;
  titles?: string[];
  excerpt?: string;
}

/** What a chat card gets: from a Share File result or the end of a turn. */
export interface ProducedFileRef {
  id: string;
  name: string;
  kind: FileKind;
  mime_type: string;
  size: number;
  source: FileSource;
  /** The agent shared it with Share File. */
  shared: boolean;
  /** Shown as a card (shared, or picked as a likely result); otherwise it's on the shelf. */
  shown: boolean;
  caption?: string;
  thumb?: boolean;
  info?: FileInfo;
}

export interface ProducedFileDetails extends ProducedFileRef {
  created: string;
  session_id: string;
  task_id: string;
  agent: { id: string; name: string } | null;
  path: string;
  /** A copy is here now (content links work without copying it in first). */
  available: boolean;
  url?: string;
  thumb_url?: string;
}

export interface SheetPreview { name: string; rows: string[][]; total_rows: number }
export interface ArchiveEntry { name: string; size: number; dir: boolean }
export interface FilePreview {
  kind: string;
  sheets?: SheetPreview[];
  entries?: ArchiveEntry[];
  count?: number;
  unpacked_size?: number;
  text?: string;
  excerpt?: string;
  slides?: number;
  titles?: string[];
}

const KINDS = new Set<FileKind>(['image', 'video', 'audio', 'pdf', 'doc', 'slides', 'sheet', 'text', 'html', 'data',
  'archive', 'model', 'font', 'code', 'other']);

/** Refs from the server (tool outcomes, turn entries), checked. */
export function parseFileRefs(value: unknown): ProducedFileRef[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out: ProducedFileRef[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const r = item as Record<string, unknown>;
    if (typeof r.id !== 'string' || typeof r.name !== 'string') continue;
    const kind = KINDS.has(r.kind as FileKind) ? (r.kind as FileKind) : 'other';
    out.push({
      id: r.id,
      name: r.name,
      kind,
      mime_type: typeof r.mime_type === 'string' ? r.mime_type : 'application/octet-stream',
      size: typeof r.size === 'number' ? r.size : 0,
      source: r.source === 'sandbox' || r.source === 'pc' ? r.source : 'server',
      shared: r.shared === true,
      shown: r.shown === true,
      caption: typeof r.caption === 'string' ? r.caption : undefined,
      thumb: r.thumb === true,
      info: r.info && typeof r.info === 'object' ? (r.info as FileInfo) : undefined,
    });
  }
  return out.length ? out : undefined;
}

/** Make a link the API returned ("/api/v1/...") absolute. */
export const fileSrc = (path: string): string => `${serverOrigin()}${path}`;

export async function getFile(id: string, fetch = false): Promise<ProducedFileDetails> {
  const { data } = await api.get<ProducedFileDetails>(`/produced-files/${encodeURIComponent(id)}`, { params: fetch ? { fetch: true } : undefined });
  return data;
}

// Links in a file's details are signed for a few hours; refetch well before.
const FRESH_MS = 4 * 60 * 60 * 1000;
const details = new Map<string, { at: number; value: Promise<ProducedFileDetails> }>();

/** A file's details (with its signed links), shared by every card that shows it. */
export function loadDetails(id: string, force = false): Promise<ProducedFileDetails> {
  const hit = details.get(id);
  if (hit && !force && Date.now() - hit.at < FRESH_MS) return hit.value;
  const value = getFile(id);
  details.set(id, { at: Date.now(), value });
  value.catch(() => details.delete(id));
  return value;
}

export function forgetDetails(id: string) {
  details.delete(id);
}


export async function getPreview(id: string): Promise<FilePreview> {
  const { data } = await api.get<FilePreview>(`/produced-files/${encodeURIComponent(id)}/preview`);
  return data;
}

export async function listFiles(params: { session_id?: string; task_id?: string }): Promise<ProducedFileDetails[]> {
  const { data } = await api.get<{ files: ProducedFileDetails[] }>('/produced-files', { params });
  return data.files || [];
}

export async function forgetFile(id: string): Promise<void> {
  await api.delete(`/produced-files/${encodeURIComponent(id)}`);
}

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** Download one file (copied in from the sandbox or PC first when needed). */
export async function downloadFile(id: string): Promise<void> {
  const file = await getFile(id, true);
  if (!file.url) throw new Error(tr('ui.producedFiles.notAvailable'));
  const a = document.createElement('a');
  a.href = `${fileSrc(file.url)}?download=true`;
  a.download = file.name;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Several files in one zip. */
export async function downloadZip(ids: string[], name = 'files'): Promise<void> {
  const { data } = await api.post('/produced-files/zip', { ids, name }, { responseType: 'blob' });
  saveBlob(data as Blob, `${name}.zip`);
}

export function fmtSize(bytes: number): string {
  if (!bytes) return '';
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(bytes >= 10 * 1024 ** 2 ? 0 : 1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function fmtDuration(seconds?: number): string {
  if (!seconds || !Number.isFinite(seconds)) return '';
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`;
}

/** One short line under a file's name: "1080p · 18 MB", "3 pages", "2 sheets · 140 rows". */
export function fileMeta(file: Pick<ProducedFileRef, 'kind' | 'size' | 'info'>): string {
  const info = file.info || {};
  const parts: string[] = [];
  if (file.kind === 'image' && info.width && info.height) parts.push(`${info.width}×${info.height}`);
  if (file.kind === 'video' && info.height) parts.push(`${info.height}p`);
  if ((file.kind === 'video' || file.kind === 'audio') && info.duration) parts.push(fmtDuration(info.duration));
  if (file.kind === 'pdf' && info.pages) parts.push(trn(info.pages, 'ui.producedFiles.pages_one', 'ui.producedFiles.pages_other'));
  if (file.kind === 'slides' && info.slides) parts.push(trn(info.slides, 'ui.producedFiles.slides_one', 'ui.producedFiles.slides_other'));
  if (file.kind === 'sheet' && info.sheets && info.sheets > 1) parts.push(trn(info.sheets, 'ui.producedFiles.sheets_one', 'ui.producedFiles.sheets_other'));
  if (file.kind === 'sheet' && info.rows) parts.push(trn(info.rows, 'ui.producedFiles.rows_one', 'ui.producedFiles.rows_other'));
  if (file.kind === 'archive' && info.count) parts.push(trn(info.count, 'ui.producedFiles.filesInside_one', 'ui.producedFiles.filesInside_other'));
  const size = fmtSize(file.size);
  if (size) parts.push(size);
  return parts.join(' · ');
}

export const sourceLabel = (source: FileSource): string => (
  source === 'sandbox' ? tr('ui.producedFiles.sourceSandbox')
    : source === 'pc' ? tr('ui.producedFiles.sourcePc')
      : tr('ui.producedFiles.sourceServer')
);

const OPEN_EVENT = 'cognitrix:open-produced-file';

/** Ask the page on screen to open a file in its side panel (or the app-wide
 *  viewer when the page has no panel). False when nothing took it. */
export function requestOpenFile(id: string): boolean {
  const event = new CustomEvent(OPEN_EVENT, { detail: { id }, cancelable: true, bubbles: true });
  return !document.dispatchEvent(event);
}

function listener(handler: (id: string) => void, onlyIfUnhandled: boolean) {
  return (event: Event) => {
    const id = (event as CustomEvent<{ id?: string }>).detail?.id;
    if (!id || (onlyIfUnhandled && event.defaultPrevented)) return;
    event.preventDefault();
    handler(id);
  };
}

/** A page with a side panel opens files there. */
export function onOpenFile(handler: (id: string) => void): () => void {
  const fn = listener(handler, false);
  document.addEventListener(OPEN_EVENT, fn);
  return () => document.removeEventListener(OPEN_EVENT, fn);
}

/** The app-wide viewer: only files no page opened (the event reaches the window last). */
export function onOpenFileElsewhere(handler: (id: string) => void): () => void {
  const fn = listener(handler, true);
  window.addEventListener(OPEN_EVENT, fn);
  return () => window.removeEventListener(OPEN_EVENT, fn);
}
