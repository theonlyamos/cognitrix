import { useEffect, useRef, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { isDesktop } from '@/lib/desktop';
import {
  checkFolder,
  folderName,
  pickFolder,
  recentFolders,
  rememberFolder,
  withPrimary,
  type WorkFolder,
} from '@/lib/folders';
import { cn } from '@/lib/utils';

const FolderIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
);

interface Props {
  folders: WorkFolder[];
  onChange: (folders: WorkFolder[]) => void | Promise<void>;
  /** Shown when the list is empty (e.g. "private scratch folder"). */
  emptyLabel?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * Working folders as chips: click a chip to switch read only / read & write,
 * make it primary or remove it; "+ folder" adds one (native picker in the
 * desktop app, typed path in a browser).
 */
export default function FolderChips({ folders, onChange, emptyLabel, disabled, className }: Props) {
  const [adding, setAdding] = useState(false);
  const [menu, setMenu] = useState<string | null>(null);
  const [path, setPath] = useState('');
  const [allowWrite, setAllowWrite] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!adding && !menu) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setAdding(false);
        setMenu(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setAdding(false);
        setMenu(null);
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [adding, menu]);

  const update = async (next: WorkFolder[]) => {
    setError('');
    setBusy(true);
    try {
      await onChange(withPrimary(next));
    } catch (e) {
      setError(errorMessage(e, 'Could not change the folders.'));
    } finally {
      setBusy(false);
    }
  };

  const add = async (raw: string) => {
    const value = raw.trim();
    if (!value) return;
    setError('');
    setBusy(true);
    try {
      const checked = await checkFolder(value, allowWrite ? 'write' : 'read');
      rememberFolder(checked.path);
      const rest = folders.filter((f) => f.path !== checked.path);
      await onChange(withPrimary([...rest, { ...checked, primary: rest.length === 0 }]));
      setPath('');
      setAdding(false);
    } catch (e) {
      setError(errorMessage(e, 'Could not add that folder.'));
    } finally {
      setBusy(false);
    }
  };

  const browse = async () => {
    const picked = await pickFolder();
    if (picked) await add(picked);
  };

  const recents = recentFolders().filter((p) => !folders.some((f) => f.path === p));

  return (
    <div ref={rootRef} className={cn('relative flex flex-wrap items-center gap-1.5', className)}>
      {folders.length === 0 && emptyLabel && (
        <span className="flex items-center gap-1.5 rounded-full border border-dashed border-line px-2.5 py-0.5 text-[11.5px] text-fg-dim">
          <FolderIcon /> {emptyLabel}
        </span>
      )}
      {folders.map((f) => {
        const write = f.access === 'write';
        return (
          <div key={f.path} className="relative">
            <button
              type="button"
              disabled={disabled}
              onClick={() => { setAdding(false); setMenu(menu === f.path ? null : f.path); }}
              title={`${f.path}\n${write ? 'Read & write' : 'Read only'}${f.primary ? ' · primary (shell starts here)' : ''}`}
              aria-haspopup="menu"
              aria-expanded={menu === f.path}
              className="flex min-h-8 items-center gap-1.5 rounded-full border border-line bg-panel px-2.5 py-0.5 text-[11.5px] text-fg transition-colors hover:border-fg-dim disabled:opacity-60"
            >
              <span aria-hidden className={cn('h-1.5 w-1.5 rounded-full', write ? 'bg-accent' : 'bg-[var(--syn-alt)]')} />
              <span className="max-w-[12rem] truncate">{folderName(f)}</span>
              <span className={cn('font-mono text-[10px]', write ? 'text-accent-ink' : 'text-[var(--syn-alt)]')}>{write ? 'write' : 'read'}</span>
              {f.primary && folders.length > 1 && <span className="font-mono text-[10px] text-fg-dim">· primary</span>}
            </button>
            {menu === f.path && (
              <div role="menu" className="absolute bottom-full left-0 z-30 mb-1.5 w-64 rounded-lg border border-line bg-panel p-1.5 shadow-lg">
                <div className="truncate px-2 py-1 font-mono text-[10.5px] text-fg-dim" title={f.path}>{f.path}</div>
                <button role="menuitem" type="button" className="w-full rounded px-2 py-1.5 text-left text-[12.5px] hover:bg-panel-2"
                  onClick={() => { setMenu(null); void update(folders.map((x) => x.path === f.path ? { ...x, access: write ? 'read' : 'write' } : x)); }}>
                  {write ? 'Make read only' : 'Allow writing (read & write)'}
                </button>
                {!f.primary && (
                  <button role="menuitem" type="button" className="w-full rounded px-2 py-1.5 text-left text-[12.5px] hover:bg-panel-2"
                    onClick={() => { setMenu(null); void update(withPrimary(folders, f.path)); }}>
                    Make primary (shell starts here)
                  </button>
                )}
                <button role="menuitem" type="button" className="w-full rounded px-2 py-1.5 text-left text-[12.5px] text-danger-ink hover:bg-panel-2"
                  onClick={() => { setMenu(null); void update(folders.filter((x) => x.path !== f.path)); }}>
                  Remove
                </button>
              </div>
            )}
          </div>
        );
      })}
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => { setMenu(null); setAdding((v) => !v); }}
        aria-expanded={adding}
        className="flex min-h-8 items-center gap-1 rounded-full border border-dashed border-line px-2.5 py-0.5 text-[11.5px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg disabled:opacity-60"
      >
        + folder
      </button>
      {adding && (
        <div role="dialog" aria-label="Add a folder" className="absolute bottom-full left-0 z-30 mb-1.5 flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2 rounded-lg border border-line bg-panel p-2.5 shadow-lg">
          {recents.length > 0 && (
            <div className="flex flex-col">
              <div className="px-1 pb-1 font-mono text-[10px] tracking-[0.12em] text-fg-dim">RECENT</div>
              {recents.map((p) => (
                <button key={p} type="button" onClick={() => void add(p)}
                  className="truncate rounded px-2 py-1.5 text-left font-mono text-[11.5px] hover:bg-panel-2" title={p}>
                  {p}
                </button>
              ))}
            </div>
          )}
          {isDesktop() && (
            <button type="button" onClick={() => void browse()} disabled={busy}
              className="rounded bg-accent px-3 py-2 text-[12.5px] font-medium text-accent-foreground hover:brightness-105 disabled:opacity-60">
              Browse…
            </button>
          )}
          <form className="flex gap-1.5" onSubmit={(e) => { e.preventDefault(); void add(path); }}>
            <label className="sr-only" htmlFor="folder-path">Folder path</label>
            <input id="folder-path" value={path} onChange={(e) => setPath(e.target.value)}
              placeholder={isDesktop() ? 'or paste a folder path' : 'Folder path on the server'}
              className="min-w-0 flex-1 rounded border border-line bg-bg px-2 py-1.5 font-mono text-[11.5px] text-fg placeholder:text-fg-dim" />
            <button type="submit" disabled={busy || !path.trim()}
              className="rounded border border-line px-2.5 text-[12px] hover:bg-panel-2 disabled:opacity-60">Add</button>
          </form>
          <label className="flex items-center gap-2 px-1 text-[12px] text-fg-dim">
            <input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} className="accent-[var(--accent)]" />
            Allow writing (otherwise read only)
          </label>
        </div>
      )}
      {error && <div role="alert" className="basis-full font-mono text-[10.5px] text-danger-ink">{error}</div>}
    </div>
  );
}
