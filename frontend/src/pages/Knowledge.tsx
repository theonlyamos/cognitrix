import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { PageHeader, LoadingState, ErrorState, EmptyState } from '@/components/list-ui';
import { cn } from '@/lib/utils';

interface KnowledgeDocument {
  id: string;
  name: string;
  size_bytes: number;
  status: 'queued' | 'ready' | 'failed';
  error: string | null;
  passages: number;
}
export interface KnowledgeCollection {
  id: string;
  name: string;
  description: string;
  documents: KnowledgeDocument[];
}
interface Hit { text: string; name: string; position: number; score: number }

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
        setError(`${file.name}: ${errorMessage(e, 'could not be added.')}`);
      }
    }
    setBusy(false);
    onChanged();
  };

  const remove = async (doc: KnowledgeDocument) => {
    try {
      await api.delete(`/knowledge/${collection.id}/documents/${doc.id}`);
      onChanged();
    } catch (e) {
      setError(errorMessage(e, 'Could not remove the document.'));
    }
  };

  const removeCollection = async () => {
    if (!window.confirm(`Delete “${collection.name}” and its documents? Agents will no longer be able to search it.`)) return;
    try {
      await api.delete(`/knowledge/${collection.id}`);
      onChanged();
    } catch (e) {
      setError(errorMessage(e, 'Could not delete the collection.'));
    }
  };

  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return setHits(null);
    try {
      const res = await api.post<Hit[]>(`/knowledge/${collection.id}/search`, { query });
      setHits(res.data);
    } catch (e) {
      setError(errorMessage(e, 'Could not search.'));
    }
  };

  return (
    <section className="rounded-[10px] border border-line bg-panel" aria-label={collection.name}>
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[15px] font-semibold">{collection.name}</h2>
          <p className="text-[12px] text-fg-dim">
            {collection.description || `${collection.documents.length} document${collection.documents.length === 1 ? '' : 's'}`}
          </p>
        </div>
        <input ref={fileInput} type="file" multiple accept={ACCEPT} className="hidden" aria-label={`Add documents to ${collection.name}`} onChange={upload} />
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => fileInput.current?.click()}>
          {busy ? 'Adding…' : 'Add documents'}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={removeCollection}>Delete</Button>
      </div>
      {error && <p role="alert" className="border-t border-line px-4 py-2 text-[12.5px] text-danger-ink">{error}</p>}
      {collection.documents.length > 0 && (
        <ul className="border-t border-line">
          {collection.documents.map((doc) => (
            <li key={doc.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2 last:border-b-0">
              <span className="min-w-0 flex-1 truncate text-[13.5px]">{doc.name}</span>
              <span className={cn('font-mono text-[11px]', doc.status === 'failed' ? 'text-danger-ink' : 'text-fg-dim')}>
                {doc.status === 'queued' ? 'reading…' : doc.status === 'failed' ? doc.error || 'failed' : `${size(doc.size_bytes)} · ${doc.passages} passages`}
              </span>
              <button type="button" onClick={() => void remove(doc)} aria-label={`Remove ${doc.name}`}
                className="min-h-11 px-1 font-mono text-[11px] text-fg-dim hover:text-danger-ink md:min-h-0">remove</button>
            </li>
          ))}
        </ul>
      )}
      {collection.documents.some((d) => d.status === 'ready') && (
        <form onSubmit={search} className="flex gap-2 border-t border-line px-4 py-3">
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Try a search, the way an agent would" aria-label={`Search ${collection.name}`} />
          <Button type="submit" size="sm" variant="outline">Search</Button>
        </form>
      )}
      {hits && (
        <div className="border-t border-line px-4 py-3">
          {hits.length === 0 ? <p className="text-[13px] text-fg-dim">Nothing matches.</p> : (
            <ol className="space-y-3">
              {hits.map((hit, i) => (
                <li key={`${hit.name}-${hit.position}-${i}`}>
                  <div className="font-mono text-[11px] text-fg-dim">{hit.name} · part {hit.position + 1}</div>
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

export default function Knowledge() {
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
      setCreateError(errorMessage(e, 'Could not create the collection.'));
    }
  };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title="Knowledge" subtitle="documents your agents can search and quote from · private to you" />
      <div className="flex-1 overflow-y-auto">
        {loading && !data ? (
          <LoadingState label="loading collections…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-5 md:px-6">
            <form onSubmit={create} className="flex gap-2">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="New collection, for example Company handbook" aria-label="New collection name" maxLength={80} />
              <Button type="submit" size="sm" disabled={!name.trim()}>Create</Button>
            </form>
            {createError && <p role="alert" className="text-[12.5px] text-danger-ink">{createError}</p>}
            {collections.length === 0 ? (
              <EmptyState
                icon={<span aria-hidden className="font-mono text-lg">¶</span>}
                title="No collections yet"
                desc="Create a collection, add PDFs, Word files or notes, then turn it on for an agent on its Connections tab. Files are read on this computer."
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
