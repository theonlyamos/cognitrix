import { useEffect, useId, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { isDesktop } from '@/lib/desktop';
import {
  checkFolder,
  folderInfo,
  folderName,
  pickFolder,
  recentFolders,
  rememberFolder,
  withPrimary,
  type WorkFolder,
} from '@/lib/folders';
import type { TabProps } from '@/lib/agentDraft';
import { Card, Check, Eyebrow, Pill, buttonClass, inputClass, linkButtonClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';

/** Same wording as folder_prompt() in cognitrix/common/folders.py. */
function folderPrompt(folders: WorkFolder[] | null, workingFolder: string): string {
  if (folders === null) {
    return `You can work with files in the app's working folder${workingFolder ? `: ${workingFolder}` : ''} (read & write).`;
  }
  if (folders.length === 0) {
    return 'You can work with files in a private scratch folder for this chat (read & write). It is deleted with the chat.';
  }
  const lines = ['You can work with files in these folders:'];
  for (const f of folders) {
    lines.push(`- ${folderName(f)}: ${f.path} (${f.access === 'write' ? 'read & write' : 'read only'}${f.primary ? ', primary' : ''})`);
  }
  lines.push('Relative paths and shell commands start in the primary folder. Paths outside these folders are refused, and read only folders can\'t be changed.');
  return lines.join('\n');
}

export default function FoldersTab({ draft, update }: TabProps) {
  const pathId = useId();
  const [workingFolder, setWorkingFolder] = useState('');
  const [adding, setAdding] = useState(false);
  const [path, setPath] = useState('');
  const [allowWrite, setAllowWrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    folderInfo().then((i) => setWorkingFolder(i.working_folder)).catch(() => {});
  }, []);

  const folders = draft.folders;
  const set = (next: WorkFolder[] | null) => update({ folders: next === null ? null : withPrimary(next) });

  const add = async (raw: string) => {
    const value = raw.trim();
    if (!value) return;
    setBusy(true);
    setError('');
    try {
      const checked = await checkFolder(value, allowWrite ? 'write' : 'read');
      rememberFolder(checked.path);
      const rest = (folders || []).filter((f) => f.path !== checked.path);
      set([...rest, { ...checked, primary: rest.length === 0 }]);
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

  const recents = recentFolders().filter((p) => !(folders || []).some((f) => f.path === p));

  return (
    <div className="flex flex-col gap-[18px]">
      <Card title="Default folders" description="Every new chat and task with this agent starts with these.">
        {folders === null ? (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-line px-3.5 py-3 text-[13.5px]">
            <span className="min-w-[220px] flex-1 text-fg-dim">
              Uses the app's working folder{workingFolder && <> <span className="font-mono text-[12.5px] text-fg">{workingFolder}</span></>} (read &amp; write).
            </span>
            <button type="button" className={buttonClass}
              onClick={() => set(workingFolder ? [{ path: workingFolder, access: 'write', primary: true }] : [])}>
              Choose folders
            </button>
          </div>
        ) : (
          <>
            <div className="overflow-hidden rounded-lg border border-line">
              <div className="hidden grid-cols-[170px_minmax(0,1fr)_150px_36px] gap-3 bg-panel-2 px-3.5 py-2 text-[12px] text-fg-dim md:grid">
                <span>Name</span><span>Path</span><span>Access</span><span />
              </div>
              {folders.length === 0 ? (
                <p className="border-line px-3.5 py-3 text-[13px] text-fg-dim md:border-t">
                  No folders: each chat gets a private scratch folder, deleted with the chat.
                </p>
              ) : folders.map((f) => {
                const write = f.access === 'write';
                return (
                  <div key={f.path} className="grid grid-cols-[minmax(0,1fr)_36px] items-center gap-x-3 gap-y-1.5 border-t border-line px-3.5 py-3 first:border-t-0 md:grid-cols-[170px_minmax(0,1fr)_150px_36px] md:first:border-t">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="truncate text-[15px] font-semibold">{folderName(f)}</span>
                      {f.primary ? <Pill tone="accent">primary</Pill> : folders.length > 1 && (
                        <button type="button" onClick={() => set(withPrimary(folders, f.path))} className="text-[11.5px] text-fg-dim hover:text-fg hover:underline">
                          make primary
                        </button>
                      )}
                    </div>
                    <span className="col-span-2 row-start-2 truncate font-mono text-[12px] text-fg-dim md:col-span-1 md:row-start-auto" title={f.path}>{f.path}</span>
                    <button type="button"
                      onClick={() => set(folders.map((x) => (x.path === f.path ? { ...x, access: write ? 'read' : 'write' } : x)))}
                      aria-label={`${folderName(f)}: ${write ? 'read & write' : 'read only'}. Switch access`}
                      className={cn('col-span-2 row-start-3 min-h-10 rounded-md border text-[13px] md:col-span-1 md:row-start-auto md:min-h-8',
                        write ? 'border-accent-ink bg-accent/10 font-medium text-accent-ink' : 'border-[var(--syn-alt)] bg-[var(--syn-alt)]/10 text-[var(--syn-alt)]')}>
                      {write ? 'Read & write' : 'Read only'}
                    </button>
                    <button type="button" onClick={() => set(folders.filter((x) => x.path !== f.path))} aria-label={`Remove ${folderName(f)}`}
                      className="col-start-2 row-start-1 grid h-9 w-9 place-items-center rounded-md border border-line text-[12px] hover:bg-panel-2 md:col-start-auto md:row-start-auto md:h-[30px] md:w-[30px]">
                      ✕
                    </button>
                  </div>
                );
              })}
            </div>

            {adding ? (
              <div className="mt-3.5 flex flex-col gap-2.5 rounded-lg border border-dashed border-line p-3.5">
                {recents.length > 0 && (
                  <div className="flex flex-col">
                    <Eyebrow className="pb-1 text-[10px]">RECENT</Eyebrow>
                    {recents.map((p) => (
                      <button key={p} type="button" onClick={() => void add(p)} disabled={busy} title={p}
                        className="truncate rounded px-2 py-1.5 text-left font-mono text-[12px] hover:bg-panel-2">{p}</button>
                    ))}
                  </div>
                )}
                <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void add(path); }}>
                  <label htmlFor={pathId} className="sr-only">Folder path</label>
                  <input id={pathId} value={path} onChange={(e) => setPath(e.target.value)} autoFocus
                    placeholder={isDesktop() ? 'Paste a folder path' : 'Folder path on the server'}
                    className={cn(inputClass, 'min-w-[220px] flex-1 font-mono text-[12.5px]')} />
                  {isDesktop() && <button type="button" onClick={() => void browse()} disabled={busy} className={buttonClass}>Browse…</button>}
                  <button type="submit" disabled={busy || !path.trim()} className={buttonClass}>Add</button>
                  <button type="button" onClick={() => setAdding(false)} className={buttonClass}>Cancel</button>
                </form>
                <label className="flex items-center gap-2 text-[13px] text-fg-dim">
                  <input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
                  Allow writing (otherwise read only)
                </label>
              </div>
            ) : (
              <div className="mt-3.5 flex flex-wrap items-center gap-2">
                <button type="button" onClick={() => setAdding(true)} className={buttonClass}>+ Add folder…</button>
                <button type="button" onClick={() => set(null)} className={linkButtonClass}>Go back to the app's working folder</button>
              </div>
            )}
            {error && <p role="alert" className="mt-2 font-mono text-[12px] text-danger-ink">{error}</p>}
          </>
        )}
      </Card>

      <Card title="Rules">
        <Check
          checked={draft.canRequestFolders}
          onChange={(canRequestFolders) => update({ canRequestFolders })}
          label="Let the agent ask for other folders"
          detail="It can request a folder or write access with a reason. You approve it in the chat or the Inbox. Unattended task runs pause until someone answers."
        />
        <p className="mt-3.5 text-[13px] text-fg-dim">
          Chats keep their own copy of these folders, so changing them here doesn't touch chats already open.
        </p>
      </Card>

      <Card>
        <Eyebrow>WHAT THE AGENT IS TOLD</Eyebrow>
        <pre className="mt-3 whitespace-pre-wrap font-mono text-[12.5px] leading-relaxed text-fg">{folderPrompt(folders, workingFolder)}</pre>
      </Card>
    </div>
  );
}
