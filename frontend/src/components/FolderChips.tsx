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
import { composerBtn, composerPopover } from '@/components/composerStyles';

const FolderIcon = ({ size = 12 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
);

const ChevronIcon = () => (
  <svg aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="hidden opacity-60 sm:block"><path d="m6 9 6 6 6-6" /></svg>
);

interface Props {
  folders: WorkFolder[];
  onChange: (folders: WorkFolder[]) => void | Promise<void>;
  /** Shown when the list is empty (e.g. "private scratch folder"). */
  emptyLabel?: string;
  disabled?: boolean;
  className?: string;
  /** 'button' shows one button ("cognitrix +2") that opens the folder list;
   *  used in the chat box so the row never wraps. */
  variant?: 'chips' | 'button';
}

/**
 * Working folders as chips: click a chip to switch read only / read & write,
 * make it primary or remove it; "+ folder" adds one (native picker in the
 * desktop app, typed path in a browser).
 */
export default function FolderChips({ folders, onChange, emptyLabel, disabled, className, variant = 'chips' }: Props) {
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState<string | null>(null);
  const [path, setPath] = useState('');
  const [allowWrite, setAllowWrite] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!adding && !menu && !open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setAdding(false);
        setMenu(null);
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setAdding(false);
        setMenu(null);
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [adding, menu, open]);

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

  const addForm = (floating: boolean) => (
        <div role="dialog" aria-label="Add a folder" className={cn('flex flex-col gap-2', floating && 'absolute bottom-full left-0 z-30 mb-1.5 w-[min(22rem,calc(100vw-2rem))] rounded-lg border border-line bg-panel p-2.5 shadow-lg')}>
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
  );

  if (variant === 'button') {
    const main = folders.find((f) => f.primary) || folders[0];
    const label = main ? folderName(main) : (emptyLabel || 'No folder');
    const close = () => { setOpen(false); setAdding(false); };
    return (
      <div ref={rootRef} className={cn('min-w-0', className)}>
        <button
          type="button"
          disabled={disabled}
          onClick={() => { setAdding(false); setOpen((v) => !v); }}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={main ? `Folders: ${folders.map(folderName).join(', ')}. Change` : `Folders: ${label}. Change`}
          title={folders.length ? folders.map((f) => `${f.path} (${f.access === 'write' ? 'read & write' : 'read only'})`).join('\n') : 'The agent works in a private scratch folder. Click to add one of your folders.'}
          className={cn(composerBtn, open && 'bg-panel-2 text-fg')}
        >
          <FolderIcon size={15} />
          <span className={cn('hidden max-w-[11rem] truncate sm:inline', main && 'text-fg')}>{label}</span>
          {folders.length > 1 && <span className="font-mono text-[11px] text-fg-dim"><span className="hidden sm:inline">+{folders.length - 1}</span><span className="sm:hidden">{folders.length}</span></span>}
          <ChevronIcon />
        </button>
        {open && (
          <>
            <div aria-hidden className="fixed inset-0 z-40 bg-black/40 md:hidden" onClick={close} />
            <div role="dialog" aria-label="Folders this chat can use" className={cn(composerPopover, 'md:w-[22rem]')}>
              <div aria-hidden className="mx-auto mb-2 h-1 w-9 rounded-full bg-line md:hidden" />
              {adding ? (
                <div className="p-1">
                  <button type="button" onClick={() => setAdding(false)} className="mb-2 text-[12px] text-fg-dim hover:text-fg">‹ Folders</button>
                  {addForm(false)}
                </div>
              ) : (
                <>
                  <div className="px-2 pb-1 pt-1.5 font-mono text-[10.5px] tracking-[0.1em] text-fg-dim">FOLDERS THIS CHAT CAN USE</div>
                  {folders.length === 0 && (
                    <p className="px-2 py-1.5 text-[12.5px] text-fg-dim">None yet. The agent works in a private scratch folder that only this chat sees.</p>
                  )}
                  {folders.map((f) => {
                    const write = f.access === 'write';
                    return (
                      <div key={f.path} className="group flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-panel-2">
                        <span className="text-fg-dim"><FolderIcon size={15} /></span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] text-fg">
                            {folderName(f)}
                            {f.primary && folders.length > 1 && <span className="ml-1.5 font-mono text-[10px] text-fg-dim">primary</span>}
                          </span>
                          <span className="block truncate font-mono text-[10.5px] text-fg-dim" title={f.path}>{f.path}</span>
                          {!f.primary && (
                            <button type="button" disabled={busy} onClick={() => void update(withPrimary(folders, f.path))}
                              className="min-h-8 text-[11.5px] text-fg-dim underline-offset-2 hover:text-fg hover:underline md:min-h-0">
                              Make primary (shell starts here)
                            </button>
                          )}
                        </span>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void update(folders.map((x) => x.path === f.path ? { ...x, access: write ? 'read' : 'write' } : x))}
                          title={write ? 'Read & write. Click to make it read only.' : 'Read only. Click to allow writing.'}
                          aria-label={`${folderName(f)}: ${write ? 'read and write' : 'read only'}. Switch`}
                          className={cn('min-h-9 rounded-md border border-line px-2 font-mono text-[10.5px] transition-colors hover:border-fg-dim md:min-h-6', write ? 'text-accent-ink' : 'text-[var(--syn-alt)]')}
                        >
                          {write ? 'write' : 'read'}
                        </button>
                        <button type="button" disabled={busy} onClick={() => void update(folders.filter((x) => x.path !== f.path))}
                          aria-label={`Remove ${folderName(f)}`} title="Remove"
                          className="grid h-9 w-9 place-items-center rounded-md text-fg-dim hover:bg-panel hover:text-danger-ink md:h-6 md:w-6">
                          <svg aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
                        </button>
                      </div>
                    );
                  })}
                  <div className="mx-1 my-1 h-px bg-line" />
                  <button type="button" disabled={busy} onClick={() => setAdding(true)}
                    className="flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-left text-[13px] text-fg hover:bg-panel-2 md:min-h-9">
                    <svg aria-hidden width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
                    Add folder
                  </button>
                </>
              )}
              {error && <div role="alert" className="px-2 pt-1 font-mono text-[10.5px] text-danger-ink">{error}</div>}
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div ref={rootRef} className={cn('relative flex flex-wrap items-center gap-1.5', className)}>
      {folders.length === 0 && emptyLabel && (
        <span className="hidden items-center gap-1.5 whitespace-nowrap rounded-full border border-dashed border-line px-2.5 py-0.5 text-[11.5px] text-fg-dim sm:flex">
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
        className="flex min-h-8 items-center gap-1 whitespace-nowrap rounded-full border border-dashed border-line px-2.5 py-0.5 text-[11.5px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg disabled:opacity-60"
      >
        + folder
      </button>
      {adding && addForm(true)}
      {error && <div role="alert" className="basis-full font-mono text-[10.5px] text-danger-ink">{error}</div>}
    </div>
  );
}
