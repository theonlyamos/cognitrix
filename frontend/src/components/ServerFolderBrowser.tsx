import { useCallback, useEffect, useId, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { browseFolders, createFolder, type BrowseListing } from '@/lib/folders';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

interface Props {
  /** Called with the folder the person chose. */
  onPick: (path: string) => void | Promise<void>;
  onCancel: () => void;
  busy?: boolean;
  /** Where to open; defaults to the starting folders. */
  start?: string | null;
  className?: string;
}

const FolderGlyph = () => (
  <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
);

/**
 * Browse folders on the machine running Cognitrix, for the web app (the
 * desktop app uses the system's own folder picker). Only folders the server
 * allows are listed; the server checks every path again when one is added.
 */
export default function ServerFolderBrowser({ onPick, onCancel, busy, start = null, className }: Props) {
  const newId = useId();
  const [listing, setListing] = useState<BrowseListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState('');
  const [newName, setNewName] = useState<string | null>(null);

  const open = useCallback(async (path: string | null, showHidden = hidden) => {
    setLoading(true);
    setError('');
    try {
      const next = await browseFolders(path, showHidden);
      // One starting folder: open it straight away.
      if (next.path === null && next.entries.length === 1) {
        setListing(await browseFolders(next.entries[0].path, showHidden));
      } else {
        setListing(next);
      }
    } catch (e) {
      setError(errorMessage(e, tr('ui.serverFolderBrowser.couldnTOpenThatFolder')));
    } finally {
      setLoading(false);
    }
  }, [hidden]);

  useEffect(() => { void open(start); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const current = listing?.path ?? null;

  const make = async () => {
    if (!current || !newName?.trim()) return;
    setError('');
    try {
      const made = await createFolder(current, newName.trim());
      setNewName(null);
      await open(made.path);
    } catch (e) {
      setError(errorMessage(e, tr('ui.serverFolderBrowser.couldnTCreateTheFolder')));
    }
  };

  return (
    <div role="group" aria-label={tr('ui.serverFolderBrowser.browseFoldersOnTheServer')} className={cn('flex min-w-0 flex-col gap-2', className)}>
      <div className="flex items-center gap-1.5">
        <button type="button" onClick={() => void open(listing?.parent ?? null)}
          disabled={loading || current === null}
          aria-label={tr('ui.serverFolderBrowser.upOneFolder')} title={tr('ui.serverFolderBrowser.upOneFolder')}
          className="grid h-8 w-8 shrink-0 place-items-center rounded border border-line text-fg-dim hover:bg-panel-2 hover:text-fg disabled:opacity-40">
          <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m18 15-6-6-6 6" /></svg>
        </button>
        <div className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-fg" title={current || ''} dir="rtl">
          <bdi>{current || tr('ui.serverFolderBrowser.foldersOnTheServer')}</bdi>
        </div>
      </div>

      <ul aria-label={tr('ui.serverFolderBrowser.folders')} aria-busy={loading} className="max-h-56 min-h-[6rem] overflow-y-auto rounded border border-line bg-bg py-1">
        {loading && !listing && <li className="px-2.5 py-1.5 text-[12px] text-fg-dim">{tr('ui.serverFolderBrowser.loading')}</li>}
        {listing && listing.entries.length === 0 && !loading && (
          <li className="px-2.5 py-1.5 text-[12px] text-fg-dim">{tr('ui.serverFolderBrowser.noFoldersInside')}</li>
        )}
        {listing?.entries.map((e) => (
          <li key={e.path}>
            <button type="button" onClick={() => void open(e.path)} disabled={loading}
              title={e.path}
              className="flex min-h-9 w-full items-center gap-2 px-2.5 text-left text-[12.5px] text-fg hover:bg-panel-2 md:min-h-7">
              <span className="text-fg-dim"><FolderGlyph /></span>
              <span className="min-w-0 flex-1 truncate">{e.name}</span>
              {!e.writable && <span className="font-mono text-[10px] text-fg-dim">{tr('ui.serverFolderBrowser.readOnly')}</span>}
            </button>
          </li>
        ))}
        {listing?.truncated && <li className="px-2.5 py-1.5 text-[11.5px] text-fg-dim">{tr('ui.serverFolderBrowser.onlyTheFirst1000')}</li>}
      </ul>

      {newName !== null ? (
        <form className="flex gap-1.5" onSubmit={(e) => { e.preventDefault(); void make(); }}>
          <label htmlFor={newId} className="sr-only">{tr('ui.serverFolderBrowser.newFolderName')}</label>
          <input id={newId} value={newName} onChange={(e) => setNewName(e.target.value)} autoFocus placeholder={tr('ui.serverFolderBrowser.newFolderName')}
            className="min-w-0 flex-1 rounded border border-line bg-bg px-2 py-1.5 text-[12px] text-fg placeholder:text-fg-dim" />
          <button type="submit" disabled={!newName.trim()} className="rounded border border-line px-2.5 text-[12px] hover:bg-panel-2 disabled:opacity-60">{tr('ui.serverFolderBrowser.create')}</button>
          <button type="button" onClick={() => setNewName(null)} className="rounded px-2 text-[12px] text-fg-dim hover:text-fg">{tr('ui.serverFolderBrowser.cancel')}</button>
        </form>
      ) : (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-fg-dim">
          {current && listing?.writable !== false && (
            <button type="button" onClick={() => setNewName('')} className="hover:text-fg hover:underline">{tr('ui.serverFolderBrowser.newFolder')}</button>
          )}
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={hidden} className="accent-[var(--accent)]"
              onChange={(e) => { setHidden(e.target.checked); void open(current, e.target.checked); }} />
            {tr('ui.serverFolderBrowser.showHidden')}
          </label>
        </div>
      )}

      {error && <p role="alert" className="font-mono text-[11px] text-danger-ink">{error}</p>}

      <div className="flex gap-1.5">
        <button type="button" disabled={busy || loading || !current} onClick={() => current && void onPick(current)}
          className="flex-1 rounded bg-accent px-3 py-2 text-[12.5px] font-medium text-accent-foreground hover:brightness-105 disabled:opacity-60">
          {tr('ui.serverFolderBrowser.useThisFolder')}
        </button>
        <button type="button" onClick={onCancel} className="rounded border border-line px-3 text-[12.5px] hover:bg-panel-2">{tr('ui.serverFolderBrowser.back')}</button>
      </div>
    </div>
  );
}
