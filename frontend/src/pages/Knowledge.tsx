import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { PageHeader, LoadingState, ErrorState, EmptyState } from '@/components/list-ui';
import { cn } from '@/lib/utils';
import { timeAgo } from '@/lib/agentDraft';
import { tr, trn } from '@/lib/i18n';

interface KnowledgeDocument {
  id: string;
  name: string;
  size_bytes: number;
  status: 'queued' | 'ready' | 'failed';
  error: string | null;
  passages: number;
  /** Set for a web source: the page it was read from. */
  source_url?: string | null;
  max_pages?: number;
  pages_read?: number;
  /** Days between automatic re-reads; 0 = only when asked. */
  refresh_days?: number;
  refreshed?: string | null;
}
export interface KnowledgeCollection {
  id: string;
  name: string;
  description: string;
  documents: KnowledgeDocument[];
}
interface Hit { text: string; name: string; position: number; score: number; url?: string | null }

const PAGE_CHOICES = [
  { value: 1, get label() { return tr('ui.knowledge.justThisPage'); } },
  { value: 10, get label() { return tr('ui.knowledge.thisPageLinkedPagesUp'); } },
  { value: 25, get label() { return tr('ui.knowledge.thisPageLinkedPagesUp_136d'); } },
  { value: 50, get label() { return tr('ui.knowledge.thisPageLinkedPagesUp_6dcc'); } },
];
const REFRESH_CHOICES = [
  { value: 0, get label() { return tr('ui.knowledge.refreshWhenIAsk'); } },
  { value: 1, get label() { return tr('ui.knowledge.refreshDaily'); } },
  { value: 7, get label() { return tr('ui.knowledge.refreshWeekly'); } },
  { value: 30, get label() { return tr('ui.knowledge.refreshMonthly'); } },
];
const selectClass = 'h-9 rounded-lg border border-line bg-bg px-2 text-[12.5px] text-fg md:h-8';

/** "Add a web page" form: the address, how much of the site, and how often to read it again. */
function AddWebPage({ collectionId, onAdded, onError }: { collectionId: string; onAdded: () => void; onError: (message: string) => void }) {
  const [url, setUrl] = useState('');
  const [pages, setPages] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    onError('');
    try {
      await api.post(`/knowledge/${collectionId}/urls`, { url: url.trim(), max_pages: pages, refresh_days: refresh });
      setUrl('');
      onAdded();
    } catch (e) {
      onError(errorMessage(e, tr('ui.knowledge.couldNotAddThatPage')));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3" aria-label={tr('ui.knowledge.addAWebPage')}>
      <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://docs.example.com/guide/" aria-label={tr('ui.knowledge.webPageAddress')}
        inputMode="url" className="min-w-[220px] flex-1" />
      <select value={pages} onChange={(e) => setPages(Number(e.target.value))} aria-label={tr('ui.knowledge.howMuchOfTheSite')} className={selectClass}>
        {PAGE_CHOICES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
      <select value={refresh} onChange={(e) => setRefresh(Number(e.target.value))} aria-label={tr('ui.knowledge.howOftenToReadIt')} className={selectClass}>
        {REFRESH_CHOICES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
      <Button type="submit" size="sm" variant="outline" disabled={busy || !url.trim()}>{busy ? tr('ui.knowledge.adding') : tr('ui.knowledge.addPage')}</Button>
    </form>
  );
}

function docStatus(doc: KnowledgeDocument): string {
  if (doc.status === 'queued') return doc.source_url && doc.error ? `reading… ${doc.error}` : 'reading…';
  if (doc.status === 'failed') return doc.error || 'failed';
  if (doc.source_url) {
    const pages = doc.pages_read ?? 1;
    return `${pages} page${pages === 1 ? '' : 's'} · ${doc.passages} passages${doc.refreshed ? ` · read ${timeAgo(doc.refreshed)}` : ''}`;
  }
  return tr('ui.knowledge.sizePassagesPassages', { size: size(doc.size_bytes), passages: doc.passages });
}

const ACCEPT = '.pdf,.docx,.txt,.md,.markdown,.csv,.tsv,.json,.html,.htm,.log,.rst,.yaml,.yml';

const size = (bytes: number) => (bytes >= 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

function CollectionCard({ collection, onChanged }: { collection: KnowledgeCollection; onChanged: () => void }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<Hit[] | null>(null);
  const reading = collection.documents.some((d) => d.status === 'queued');

  // Files are read in the background: look again until none is waiting.
  useEffect(() => {
    if (!reading) return;
    const timer = window.setTimeout(onChanged, 2000);
    return () => window.clearTimeout(timer);
  }, [reading, collection, onChanged]);

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length) return;
    setBusy(true);
    setError('');
    for (const file of files) {
      const form = new FormData();
      form.append('file', file, file.name);
      try {
        await api.post(`/knowledge/${collection.id}/documents`, form);
      } catch (e) {
        setError(`${file.name}: ${errorMessage(e, tr('ui.knowledge.couldNotBeAdded'))}`);
      }
    }
    setBusy(false);
    onChanged();
  };

  const refresh = async (doc: KnowledgeDocument) => {
    setError('');
    try {
      await api.post(`/knowledge/${collection.id}/documents/${doc.id}/refresh`);
      onChanged();
    } catch (e) {
      setError(errorMessage(e, tr('ui.knowledge.couldNotRefreshThePage')));
    }
  };

  const setRefreshDays = async (doc: KnowledgeDocument, days: number) => {
    setError('');
    try {
      await api.patch(`/knowledge/${collection.id}/documents/${doc.id}`, { refresh_days: days });
      onChanged();
    } catch (e) {
      setError(errorMessage(e, tr('ui.knowledge.couldNotChangeHowOften')));
    }
  };

  const remove = async (doc: KnowledgeDocument) => {
    try {
      await api.delete(`/knowledge/${collection.id}/documents/${doc.id}`);
      onChanged();
    } catch (e) {
      setError(errorMessage(e, tr('ui.knowledge.couldNotRemoveTheDocument')));
    }
  };

  const removeCollection = async () => {
    if (!window.confirm(tr('ui.knowledge.deleteNameAndItsDocuments', { name: collection.name }))) return;
    try {
      await api.delete(`/knowledge/${collection.id}`);
      onChanged();
    } catch (e) {
      setError(errorMessage(e, tr('ui.knowledge.couldNotDeleteTheCollection')));
    }
  };

  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return setHits(null);
    try {
      const res = await api.post<Hit[]>(`/knowledge/${collection.id}/search`, { query });
      setHits(res.data);
    } catch (e) {
      setError(errorMessage(e, tr('ui.knowledge.couldNotSearch')));
    }
  };

  return (
    <section className="rounded-[10px] border border-line bg-panel" aria-label={collection.name}>
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[15px] font-semibold">{collection.name}</h2>
          <p className="text-[12px] text-fg-dim">
            {collection.description || trn(collection.documents.length, 'ui.count.documents_one', 'ui.count.documents_other')}
          </p>
        </div>
        <input ref={fileInput} type="file" multiple accept={ACCEPT} className="hidden" aria-label={tr('ui.knowledge.addDocumentsToName', { name: collection.name })} onChange={upload} />
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => fileInput.current?.click()}>
          {busy ? tr('ui.knowledge.adding') : tr('ui.knowledge.addDocuments')}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={removeCollection}>{tr('ui.knowledge.delete')}</Button>
      </div>
      {error && <p role="alert" className="border-t border-line px-4 py-2 text-[12.5px] text-danger-ink">{error}</p>}
      {collection.documents.length > 0 && (
        <ul className="border-t border-line">
          {collection.documents.map((doc) => (
            <li key={doc.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2 last:border-b-0">
              <span className="min-w-0 flex-1 basis-[200px]">
                <span className="block truncate text-[13.5px]">
                  {doc.source_url && <span aria-label={tr('ui.knowledge.webPage')} className="mr-1.5 font-mono text-[11px] text-accent-ink">{tr('ui.knowledge.www')}</span>}
                  {doc.name}
                </span>
                {doc.source_url && (
                  <a href={doc.source_url} target="_blank" rel="noreferrer noopener" className="block truncate font-mono text-[11px] text-fg-dim hover:text-fg hover:underline">
                    {doc.source_url}{(doc.max_pages ?? 1) > 1 ? tr('ui.knowledge.linkedPagesUpToMax', { max_pages: doc.max_pages }) : ''}
                  </a>
                )}
              </span>
              <span className={cn('font-mono text-[11px]', doc.status === 'failed' || (doc.error && doc.status === 'ready' && doc.error.startsWith('Last refresh')) ? 'text-danger-ink' : 'text-fg-dim')}
                title={doc.status === 'ready' && doc.error ? doc.error : undefined}>
                {docStatus(doc)}
              </span>
              {doc.source_url && (
                <>
                  <select value={doc.refresh_days ?? 0} onChange={(e) => void setRefreshDays(doc, Number(e.target.value))}
                    aria-label={tr('ui.knowledge.howOftenToReadName', { name: doc.name })} className={cn(selectClass, 'h-8 md:h-7 text-[11.5px]')}>
                    {REFRESH_CHOICES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                  <button type="button" onClick={() => void refresh(doc)} disabled={doc.status === 'queued'} aria-label={tr('ui.knowledge.refreshName', { name: doc.name })}
                    className="min-h-11 px-1 font-mono text-[11px] text-fg-dim hover:text-fg disabled:opacity-50 md:min-h-0">{tr('ui.knowledge.refresh')}</button>
                </>
              )}
              <button type="button" onClick={() => void remove(doc)} aria-label={tr('ui.knowledge.removeName', { name: doc.name })}
                className="min-h-11 px-1 font-mono text-[11px] text-fg-dim hover:text-danger-ink md:min-h-0">{tr('ui.knowledge.remove')}</button>
            </li>
          ))}
        </ul>
      )}
      <AddWebPage collectionId={collection.id} onAdded={onChanged} onError={setError} />
      {collection.documents.some((d) => d.status === 'ready') && (
        <form onSubmit={search} className="flex gap-2 border-t border-line px-4 py-3">
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={tr('ui.knowledge.tryASearchTheWay')} aria-label={tr('ui.knowledge.searchName', { name: collection.name })} />
          <Button type="submit" size="sm" variant="outline">{tr('ui.knowledge.search')}</Button>
        </form>
      )}
      {hits && (
        <div className="border-t border-line px-4 py-3">
          {hits.length === 0 ? <p className="text-[13px] text-fg-dim">{tr('ui.knowledge.nothingMatches')}</p> : (
            <ol className="space-y-3">
              {hits.map((hit, i) => (
                <li key={`${hit.name}-${hit.position}-${i}`}>
                  <div className="truncate font-mono text-[11px] text-fg-dim">{hit.name} · {hit.url ? hit.url : tr('ui.knowledge.partValue', { value: hit.position + 1 })}</div>
                  <p className="mt-0.5 line-clamp-4 whitespace-pre-wrap text-[13px] leading-relaxed">{hit.text}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}

/** Collections agents can read. `embedded` drops the page header: it is a tab of the Library. */
export default function Knowledge({ embedded = false }: { embedded?: boolean }) {
  const { data, loading, error, refetch } = useResource<KnowledgeCollection[]>('/knowledge');
  const [name, setName] = useState('');
  const [createError, setCreateError] = useState('');
  const collections = data ?? [];

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setCreateError('');
    try {
      await api.post('/knowledge', { name });
      setName('');
      refetch({ silent: true });
    } catch (e) {
      setCreateError(errorMessage(e, tr('ui.knowledge.couldNotCreateTheCollection')));
    }
  };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      {!embedded && <PageHeader title={tr('ui.knowledge.knowledge')} subtitle={tr('ui.knowledge.documentsAndWebPagesYour')} />}
      <div className="flex-1 overflow-y-auto">
        {loading && !data ? (
          <LoadingState label={tr('ui.knowledge.loadingCollections')} />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-5 md:px-6">
            <form onSubmit={create} className="flex gap-2">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('ui.knowledge.newCollectionForExampleCompany')} aria-label={tr('ui.knowledge.newCollectionName')} maxLength={80} />
              <Button type="submit" size="sm" disabled={!name.trim()}>{tr('ui.knowledge.create')}</Button>
            </form>
            {createError && <p role="alert" className="text-[12.5px] text-danger-ink">{createError}</p>}
            {collections.length === 0 ? (
              <EmptyState
                icon={<span aria-hidden className="font-mono text-lg">¶</span>}
                title={tr('ui.knowledge.noCollectionsYet')}
                desc={tr('ui.knowledge.createACollectionAddPdfs_7ba7')}
              />
            ) : (
              collections.map((c) => <CollectionCard key={c.id} collection={c} onChanged={() => refetch({ silent: true })} />)
            )}
          </div>
        )}
      </div>
    </div>
  );
}
