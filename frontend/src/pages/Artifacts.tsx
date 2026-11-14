import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Download, FileText, LayoutPanelTop, MessageCircle, Pin, Search, X } from 'lucide-react';
import { ArtifactPreview } from '@/components/ArtifactPreview';
import { AccessBadge, LiveBadge } from '@/components/artifacts/ArtifactViewer';
import { EmptyState, ErrorState, LoadingState, PageHeader } from '@/components/list-ui';
import { api, errorMessage } from '@/lib/api';
import { ago, parseStamp, sizeLabel, type ArtifactList, type FileItem, type ImageItem, type PageSummary } from '@/lib/artifactPages';
import { cn } from '@/lib/utils';

type Filter = 'all' | 'pages' | 'pinned' | 'live' | 'shared' | 'public' | 'images' | 'documents';
type Item =
  | { kind: 'page'; key: string; when: number; page: PageSummary }
  | { kind: 'image'; key: string; when: number; image: ImageItem }
  | { kind: 'file'; key: string; when: number; file: FileItem };

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'pages', label: 'Pages' },
  { id: 'images', label: 'Images' },
  { id: 'documents', label: 'Documents' },
  { id: 'pinned', label: 'Pinned' },
  { id: 'live', label: 'Live' },
  { id: 'shared', label: 'Shared with me' },
  { id: 'public', label: 'Public' },
];

const PAGEABLE = ['text/markdown', 'text/plain', 'text/html'];
const READABLE = /^(text\/|application\/(json|xml)$)/;
const when = (stamp: string) => parseStamp(stamp)?.getTime() ?? 0;
const card = 'flex min-w-0 flex-col rounded-[12px] border border-line bg-panel transition-colors';
const smallButton = 'inline-flex h-8 items-center gap-1 rounded-md border border-line px-2 text-[12px] hover:bg-panel-2 disabled:opacity-50';

function PageCard({ page }: { page: PageSummary }) {
  return (
    <Link to={`/artifacts/${page.id}`} className={cn(card, 'group hover:border-fg-dim')}>
      <div className="relative flex h-28 items-center justify-center rounded-t-[12px] border-b border-line bg-panel-2">
        <LayoutPanelTop size={30} className="text-fg-dim/60" aria-hidden />
        <span className="absolute left-2.5 top-2.5 flex items-center gap-1.5">
          {page.live && <LiveBadge />}
          {page.pinned && <span className="inline-flex items-center gap-1 rounded-md bg-panel px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim"><Pin size={10} />Pinned</span>}
        </span>
        <span className="absolute bottom-2.5 right-2.5 rounded-md bg-panel px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim">v{page.viewer_version}</span>
      </div>
      <div className="flex min-w-0 flex-col gap-1.5 p-3">
        <h3 className="truncate text-[14px] font-semibold group-hover:text-accent-ink" title={page.title}>{page.title}</h3>
        <p className="truncate font-mono text-[11px] text-fg-dim">
          {page.role === 'owner' ? (page.agent?.name ? `by ${page.agent.name}` : 'page') : `shared by ${page.owner.name}`} · {ago(page.updated)}
        </p>
        <div className="flex items-center gap-2">
          <AccessBadge access={page.access} />
          {page.open_comments > 0 && (
            <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-fg-dim"><MessageCircle size={11} />{page.open_comments}</span>
          )}
        </div>
      </div>
    </Link>
  );
}

function ImageCard({ image }: { image: ImageItem }) {
  return (
    <div className={card}>
      <div className="min-w-0 overflow-hidden rounded-t-[12px] border-b border-line bg-panel-2 p-2 [&_img]:max-h-40 [&_img]:w-full [&_img]:object-contain">
        <ArtifactPreview artifact={{ id: image.id, mime_type: image.mime_type, origin: 'generated', filename: undefined, width: image.width ?? undefined, height: image.height ?? undefined }} />
      </div>
      <div className="flex min-w-0 flex-col gap-1 p-3">
        <h3 className="line-clamp-2 text-[13px] font-medium" title={image.title}>{image.title}</h3>
        <p className="truncate font-mono text-[11px] text-fg-dim">
          image{image.agent?.name ? ` · by ${image.agent.name}` : ''} · {ago(image.created)}
        </p>
      </div>
    </div>
  );
}

function FileCard({ file, onOpen, onChanged }: { file: FileItem; onOpen: (file: FileItem) => void; onChanged: () => void }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try { await work(); } catch (e) { setError(errorMessage(e, 'That did not work.')); } finally { setBusy(false); }
  };
  const download = () => run(async () => {
    const { data } = await api.get(`/artifact-pages/files/${file.id}`, { params: { download: true }, responseType: 'blob' });
    const href = URL.createObjectURL(data as Blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = file.title;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  });
  const extension = file.title.includes('.') ? file.title.split('.').pop()!.toUpperCase().slice(0, 5) : 'FILE';
  return (
    <div className={card}>
      <div className="relative flex h-28 items-center justify-center rounded-t-[12px] border-b border-line bg-panel-2">
        <FileText size={30} className="text-fg-dim/60" aria-hidden />
        <span className="absolute bottom-2.5 right-2.5 rounded-md bg-panel px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim">{extension}</span>
      </div>
      <div className="flex min-w-0 flex-col gap-1.5 p-3">
        <h3 className="truncate text-[14px] font-semibold" title={file.path}>{file.title}</h3>
        <p className="truncate font-mono text-[11px] text-fg-dim">
          {sizeLabel(file.size_bytes)}{file.agent?.name ? ` · by ${file.agent.name}` : ''} · {ago(file.updated)}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {READABLE.test(file.mime_type) && <button type="button" className={smallButton} disabled={busy} onClick={() => onOpen(file)}>Open</button>}
          <button type="button" className={smallButton} disabled={busy} onClick={() => void download()} aria-label={`Download ${file.title}`}><Download size={13} /></button>
          {PAGEABLE.includes(file.mime_type) && (
            <button type="button" className={smallButton} disabled={busy} title="Make a page you can share and comment on"
              onClick={() => void run(async () => {
                const { data } = await api.post<{ id: string }>(`/artifact-pages/files/${file.id}/publish`, {});
                navigate(`/artifacts/${data.id}`);
              })}>
              Make a page
            </button>
          )}
          <button type="button" className={cn(smallButton, 'ml-auto border-transparent text-fg-dim')} disabled={busy} title="Take it off this list. The file itself stays where it is."
            onClick={() => void run(async () => { await api.delete(`/artifact-pages/files/${file.id}`); onChanged(); })}>
            Remove
          </button>
        </div>
        {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
      </div>
    </div>
  );
}

function FileReader({ file, onClose }: { file: FileItem; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    api.get(`/artifact-pages/files/${file.id}`, { responseType: 'text', transformResponse: (v) => v })
      .then(({ data }) => { if (live) setText(String(data)); })
      .catch((e) => { if (live) setError(errorMessage(e, 'Could not open that file.')); });
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => { live = false; window.removeEventListener('keydown', onKey); };
  }, [file.id, onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-0 md:p-8" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label={file.title} className="flex h-full w-full max-w-4xl flex-col overflow-hidden border-line bg-panel md:h-auto md:max-h-full md:rounded-[12px] md:border">
        <header className="flex h-12 flex-none items-center gap-2 border-b border-line px-3">
          <FileText size={15} className="text-fg-dim" aria-hidden />
          <h2 className="min-w-0 flex-1 truncate text-[14px] font-semibold">{file.title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" autoFocus className="grid h-9 w-9 place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg"><X size={16} /></button>
        </header>
        {error ? <p role="alert" className="p-4 text-[13px] text-danger-ink">{error}</p> : (
          <pre className="min-h-40 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[12.5px] leading-relaxed">{text ?? 'loading…'}</pre>
        )}
        <footer className="flex-none truncate border-t border-line px-3 py-2 font-mono text-[11px] text-fg-dim">{file.path}</footer>
      </div>
    </div>
  );
}

/** Which slice a tab of the Apps page shows. */
export type ArtifactKind = 'pages' | 'images' | 'documents' | 'shared';
const KIND_FILTERS: Record<ArtifactKind, Filter[]> = {
  pages: ['pages', 'pinned', 'live', 'public'],
  images: ['images'],
  documents: ['documents'],
  shared: ['shared'],
};
const KIND_EMPTY: Record<ArtifactKind, string> = {
  pages: 'Pages agents publish beside a chat (reports, dashboards, checklists) show up here.',
  images: 'Images agents generate show up here.',
  documents: 'Documents agents write in your chats show up here.',
  shared: 'Pages other people share with you show up here.',
};

/** Everything agents made: published pages, generated images and written
 *  documents. On its own it's the old Artifacts page; ``kind`` shows one
 *  slice of it inside a tab of the Apps page. */
export default function Artifacts({ kind }: { kind?: ArtifactKind } = {}) {
  const [data, setData] = useState<ArtifactList | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<Filter>(kind ? KIND_FILTERS[kind][0] : 'all');
  useEffect(() => { if (kind) setFilter(KIND_FILTERS[kind][0]); }, [kind]);
  const filters = kind ? FILTERS.filter((f) => KIND_FILTERS[kind].includes(f.id)) : FILTERS;
  const [query, setQuery] = useState('');
  const [reading, setReading] = useState<FileItem | null>(null);

  const load = async () => {
    try {
      const { data: list } = await api.get<ArtifactList>('/artifact-pages');
      setData(list);
      setError('');
    } catch (e) {
      setError(errorMessage(e, 'Could not load your artifacts.'));
    }
  };
  useEffect(() => { void load(); }, []);

  const items = useMemo<Item[]>(() => {
    if (!data) return [];
    const all: Item[] = [
      ...data.pages.map((page): Item => ({ kind: 'page', key: `p${page.id}`, when: when(page.updated), page })),
      ...data.images.map((image): Item => ({ kind: 'image', key: `i${image.id}`, when: when(image.created), image })),
      ...data.files.map((file): Item => ({ kind: 'file', key: `f${file.id}`, when: when(file.updated), file })),
    ];
    const needle = query.trim().toLowerCase();
    return all.filter((item) => {
      const title = item.kind === 'page' ? item.page.title : item.kind === 'image' ? item.image.title : item.file.title;
      if (needle && !title.toLowerCase().includes(needle)) return false;
      switch (filter) {
        case 'all': return true;
        case 'pages': return item.kind === 'page';
        case 'images': return item.kind === 'image';
        case 'documents': return item.kind === 'file';
        case 'pinned': return item.kind === 'page' && item.page.pinned;
        case 'live': return item.kind === 'page' && item.page.live;
        case 'shared': return item.kind === 'page' && item.page.role !== 'owner';
        case 'public': return item.kind === 'page' && item.page.access === 'public';
      }
    }).sort((a, b) => b.when - a.when);
  }, [data, filter, query]);

  const total = !data ? 0 : !kind ? data.pages.length + data.images.length + data.files.length
    : kind === 'images' ? data.images.length : kind === 'documents' ? data.files.length
    : kind === 'shared' ? data.pages.filter((p) => p.role !== 'owner').length : data.pages.length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!kind && <PageHeader title="Artifacts" subtitle="pages, images and documents your agents made" />}
      {error && !data ? <ErrorState message={error} onRetry={() => void load()} /> : !data ? <LoadingState label="loading…" /> : total === 0 ? (
        <EmptyState icon={<LayoutPanelTop size={20} />} title="Nothing here yet"
          desc={kind ? KIND_EMPTY[kind] : 'Ask an agent to make a report, a dashboard, an image or a document. What it makes shows up here.'}
          action={<Link to="/home" className="rounded-md bg-accent px-4 py-2 text-[13px] font-semibold text-[#0b0c0e]">Open a chat</Link>} />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-6">
          <div className="flex flex-col gap-3 md:flex-row md:items-center">
            <div role="tablist" aria-label="Filter artifacts" className="-mx-4 flex flex-none gap-1.5 overflow-x-auto px-4 md:mx-0 md:flex-wrap md:px-0">
              {filters.length > 1 && filters.map((f) => (
                <button key={f.id} type="button" role="tab" aria-selected={filter === f.id} onClick={() => setFilter(f.id)}
                  className={cn('h-9 flex-none whitespace-nowrap rounded-full border px-3 text-[12.5px] md:h-8',
                    filter === f.id ? 'border-fg bg-fg text-bg' : 'border-line text-fg-dim hover:text-fg')}>
                  {f.label}
                </button>
              ))}
            </div>
            <label className="relative md:ml-auto md:w-64">
              <span className="sr-only">Search artifacts</span>
              <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-dim" aria-hidden />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" type="search"
                className="h-10 w-full rounded-md border border-line bg-panel pl-8 pr-3 text-[13px] outline-none focus:border-fg-dim md:h-8" />
            </label>
          </div>
          {items.length === 0 ? (
            <p className="py-16 text-center text-[13px] text-fg-dim">Nothing matches.</p>
          ) : (
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
              {items.map((item) => item.kind === 'page' ? <PageCard key={item.key} page={item.page} />
                : item.kind === 'image' ? <ImageCard key={item.key} image={item.image} />
                : <FileCard key={item.key} file={item.file} onOpen={setReading} onChanged={() => void load()} />)}
            </div>
          )}
        </div>
      )}
      {reading && <FileReader file={reading} onClose={() => setReading(null)} />}
    </div>
  );
}
