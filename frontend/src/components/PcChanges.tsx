import { useCallback, useEffect, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { errorMessage } from '@/lib/api';
import { pcChanges, undoPcChanges, type PcChange } from '@/lib/devices';
import { tr, trn } from '@/lib/i18n';

const button = 'inline-flex min-h-11 items-center gap-1 rounded-md border border-line px-2 text-[12px] hover:bg-panel-2 disabled:opacity-50 md:min-h-7';

function shortPath(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 2 ? `…/${parts.slice(-2).join('/')}` : path;
}

/** Files agents in this chat changed on the person's PC, with a way to put them back. */
export function PcChanges({ sessionId, busy }: { sessionId: string; busy: boolean }) {
  const [changes, setChanges] = useState<PcChange[]>([]);
  const [error, setError] = useState('');
  const [working, setWorking] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);

  const load = useCallback(() => {
    pcChanges(sessionId)
      .then((list) => { setChanges(list); setError(''); })
      .catch(() => setChanges([]));
  }, [sessionId]);

  // Look again whenever a turn finishes.
  useEffect(() => { if (!busy) load(); }, [busy, load]);

  const undo = async (paths: string[] | null) => {
    setWorking(paths ? paths[0] : '*');
    setConfirmAll(false);
    try {
      const results = await undoPcChanges(sessionId, paths);
      const failed = results.filter((r) => !r.ok);
      setError(failed.length ? tr('ui.pcChanges.couldNotPutBack', { path: shortPath(failed[0].path), error: failed[0].error || '' }) : '');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setWorking(null);
      load();
    }
  };

  if (!changes.length && !error) return null;
  return (
    <div className="mt-3 border-t border-line pt-2">
      <div className="mb-1 flex items-center gap-2 text-[12px] text-fg-dim">
        <span className="flex-1">{trn(changes.length, 'ui.pcChanges.oneFileChanged', 'ui.pcChanges.filesChanged')}</span>
        {changes.length > 1 && (confirmAll ? (
          <>
            <button type="button" className={button} disabled={!!working} onClick={() => void undo(null)}>{tr('ui.pcChanges.putAllBack')}</button>
            <button type="button" className={button} onClick={() => setConfirmAll(false)}>{tr('ui.pcChanges.cancel')}</button>
          </>
        ) : (
          <button type="button" className={button} disabled={!!working} onClick={() => setConfirmAll(true)}>{tr('ui.pcChanges.undoAll')}</button>
        ))}
      </div>
      <ul className="space-y-1">
        {changes.map((c) => (
          <li key={c.path} className="flex items-center gap-2 text-[12.5px]">
            <span className="min-w-0 flex-1 truncate" title={c.path}>
              {shortPath(c.path)}
              <span className="ml-1 text-fg-dim">{c.kind === 'created' ? tr('ui.pcChanges.kindCreated') : c.kind === 'deleted' ? tr('ui.pcChanges.kindDeleted') : tr('ui.pcChanges.kindChanged')}</span>
            </span>
            <button
              type="button"
              className={button}
              disabled={!c.restorable || !!working}
              title={c.restorable ? (c.kind === 'created' ? tr('ui.pcChanges.removeTheNewFile') : tr('ui.pcChanges.putBackAsItWas')) : tr('ui.pcChanges.tooBigToUndo')}
              onClick={() => void undo([c.path])}
            >
              <RotateCcw size={12} aria-hidden />{tr('ui.pcChanges.undo')}
            </button>
          </li>
        ))}
      </ul>
      {error && <p className="mt-1 text-[12px] text-warn-ink">{error}</p>}
    </div>
  );
}
