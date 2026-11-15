import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { SheetEditor } from '@/components/boards/SheetEditor';
import { WhiteboardEditor } from '@/components/boards/WhiteboardEditor';
import { tr } from '@/lib/i18n';

interface DataDocumentProps {
  pageId: string;
  format: 'sheet' | 'whiteboard';
  /** The newest version the server has (the viewer polls it). */
  latest: number;
  onSaved?: () => void;
}

/** A sheet or whiteboard someone can edit. Loads the newest copy and loads
 *  it again when an agent publishes a new version, unless the person is in
 *  the middle of their own changes (then saving tells them to reload). */
export function DataDocument({ pageId, format, latest, onSaved }: DataDocumentProps) {
  const [doc, setDoc] = useState<{ version: number; source: string; key: number } | null>(null);
  const [error, setError] = useState('');
  /** Versions this editor saved itself: no need to reload for those. */
  const mine = useRef(0);
  const busy = useRef(false);
  const [dirty, setDirty] = useState(false);

  const load = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const { data } = await api.get<{ source: string; version: number }>(`/artifact-pages/${pageId}/source`);
      mine.current = data.version;
      setDirty(false);
      setDoc((d) => ({ version: data.version, source: data.source, key: (d?.key ?? 0) + 1 }));
      setError('');
    } catch (e) {
      setError(errorMessage(e, tr('ui.boards.dataDocument.couldNotOpenIt')));
    } finally {
      busy.current = false;
    }
  }, [pageId]);

  useEffect(() => { setDoc(null); void load(); }, [load]);

  // Someone else (usually the agent) published a newer version.
  useEffect(() => {
    if (doc && latest > mine.current && !dirty) void load();
  }, [latest, doc, dirty, load]);

  const saved = (version: number, more: boolean) => {
    mine.current = Math.max(mine.current, version);
    if (!more) setDirty(false);
    onSaved?.();
  };

  if (error && !doc) return <p role="alert" className="p-4 text-[13px] text-danger-ink">{error}</p>;
  if (!doc) return <div role="status" className="flex h-full items-center justify-center font-mono text-[12px] text-fg-dim">{tr('ui.boards.dataDocument.loading')}</div>;
  const props = {
    pageId, version: doc.version, source: doc.source,
    onSaved: saved, onReload: () => void load(), onChange: () => setDirty(true),
  };
  return format === 'sheet' ? <SheetEditor key={doc.key} {...props} /> : <WhiteboardEditor key={doc.key} {...props} />;
}
