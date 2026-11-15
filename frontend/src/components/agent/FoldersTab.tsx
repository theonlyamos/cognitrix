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
import { tr } from '@/lib/i18n';

/** Same wording as folder_prompt() in cognitrix/common/folders.py. */
function folderPrompt(folders: WorkFolder[] | null): string {
  if (folders === null || folders.length === 0) {
    return tr('ui.agent.foldersTab.youCanWorkWithFiles');
  }
  const lines = ['You can work with files in these folders:'];
  for (const f of folders) {
    lines.push(`- ${folderName(f)}: ${f.path} (${f.access === 'write' ? 'read & write' : 'read only'}${f.primary ? ', primary' : ''})`);
  }
  lines.push(tr('ui.agent.foldersTab.relativePathsAndShellCommands'));
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
      setError(errorMessage(e, tr('ui.agent.foldersTab.couldNotAddThatFolder')));
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
      <Card title={tr('ui.agent.foldersTab.defaultFolders')} description={tr('ui.agent.foldersTab.everyNewChatAndTask')}>
        {folders === null ? (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-line px-3.5 py-3 text-[13.5px]">
            <span className="min-w-[220px] flex-1 text-fg-dim">
              {tr('ui.agent.foldersTab.noDefaultFoldersEachNew')}{workingFolder && <> <span className="font-mono text-[12.5px] text-fg">{workingFolder}</span></>}.
            </span>
            <button type="button" className={buttonClass}
              onClick={() => set(workingFolder ? [{ path: workingFolder, access: 'write', primary: true }] : [])}>
              {tr('ui.agent.foldersTab.chooseFolders')}
            </button>
          </div>
        ) : (
          <>
            <div className="overflow-hidden rounded-lg border border-line">
              <div className="hidden grid-cols-[170px_minmax(0,1fr)_150px_36px] gap-3 bg-panel-2 px-3.5 py-2 text-[12px] text-fg-dim md:grid">
                <span>{tr('ui.agent.foldersTab.name')}</span><span>{tr('ui.agent.foldersTab.path')}</span><span>{tr('ui.agent.foldersTab.access')}</span><span />
              </div>
              {folders.length === 0 ? (
                <p className="border-line px-3.5 py-3 text-[13px] text-fg-dim md:border-t">
                  {tr('ui.agent.foldersTab.noFoldersEachChatGets')}
                </p>
              ) : folders.map((f) => {
                const write = f.access === 'write';
                return (
                  <div key={f.path} className="grid grid-cols-[minmax(0,1fr)_36px] items-center gap-x-3 gap-y-1.5 border-t border-line px-3.5 py-3 first:border-t-0 md:grid-cols-[170px_minmax(0,1fr)_150px_36px] md:first:border-t">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="truncate text-[15px] font-semibold">{folderName(f)}</span>
                      {f.primary ? <Pill tone="accent">{tr('ui.agent.foldersTab.primary')}</Pill> : folders.length > 1 && (
                        <button type="button" onClick={() => set(withPrimary(folders, f.path))} className="text-[11.5px] text-fg-dim hover:text-fg hover:underline">
                          {tr('ui.agent.foldersTab.makePrimary')}
                        </button>
                      )}
                    </div>
                    <span className="col-span-2 row-start-2 truncate font-mono text-[12px] text-fg-dim md:col-span-1 md:row-start-auto" title={f.path}>{f.path}</span>
                    <button type="button"
                      onClick={() => set(folders.map((x) => (x.path === f.path ? { ...x, access: write ? 'read' : 'write' } : x)))}
                      aria-label={tr('ui.agent.foldersTab.foldernameValueSwitchAccess', { folderName: folderName(f), value: write ? 'read & write' : 'read only' })}
                      className={cn('col-span-2 row-start-3 min-h-10 rounded-md border text-[13px] md:col-span-1 md:row-start-auto md:min-h-8',
                        write ? 'border-accent-ink bg-accent/10 font-medium text-accent-ink' : 'border-[var(--syn-alt)] bg-[var(--syn-alt)]/10 text-[var(--syn-alt)]')}>
                      {write ? tr('ui.agent.foldersTab.readWrite') : tr('ui.agent.foldersTab.readOnly')}
                    </button>
                    <button type="button" onClick={() => set(folders.filter((x) => x.path !== f.path))} aria-label={tr('ui.agent.foldersTab.removeFoldername', { folderName: folderName(f) })}
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
                    <Eyebrow className="pb-1 text-[10px]">{tr('ui.agent.foldersTab.recent')}</Eyebrow>
                    {recents.map((p) => (
                      <button key={p} type="button" onClick={() => void add(p)} disabled={busy} title={p}
                        className="truncate rounded px-2 py-1.5 text-left font-mono text-[12px] hover:bg-panel-2">{p}</button>
                    ))}
                  </div>
                )}
                <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void add(path); }}>
                  <label htmlFor={pathId} className="sr-only">{tr('ui.agent.foldersTab.folderPath')}</label>
                  <input id={pathId} value={path} onChange={(e) => setPath(e.target.value)} autoFocus
                    placeholder={isDesktop() ? tr('ui.agent.foldersTab.pasteAFolderPath') : tr('ui.agent.foldersTab.folderPathOnTheServer')}
                    className={cn(inputClass, 'min-w-[220px] flex-1 font-mono text-[12.5px]')} />
                  {isDesktop() && <button type="button" onClick={() => void browse()} disabled={busy} className={buttonClass}>{tr('ui.agent.foldersTab.browse')}</button>}
                  <button type="submit" disabled={busy || !path.trim()} className={buttonClass}>{tr('ui.agent.foldersTab.add')}</button>
                  <button type="button" onClick={() => setAdding(false)} className={buttonClass}>{tr('ui.agent.foldersTab.cancel')}</button>
                </form>
                <label className="flex items-center gap-2 text-[13px] text-fg-dim">
                  <input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
                  {tr('ui.agent.foldersTab.allowWritingOtherwiseReadOnly')}
                </label>
              </div>
            ) : (
              <div className="mt-3.5 flex flex-wrap items-center gap-2">
                <button type="button" onClick={() => setAdding(true)} className={buttonClass}>{tr('ui.agent.foldersTab.addFolder')}</button>
                <button type="button" onClick={() => set(null)} className={linkButtonClass}>{tr('ui.agent.foldersTab.clearDefaultFolders')}</button>
              </div>
            )}
            {error && <p role="alert" className="mt-2 font-mono text-[12px] text-danger-ink">{error}</p>}
          </>
        )}
      </Card>

      <Card title={tr('ui.agent.foldersTab.rules')}>
        <Check
          checked={draft.canRequestFolders}
          onChange={(canRequestFolders) => update({ canRequestFolders })}
          label={tr('ui.agent.foldersTab.letTheAgentAskFor')}
          detail={tr('ui.agent.foldersTab.itCanRequestAFolder')}
        />
        <div className="mt-3.5">
          <Check
            checked={draft.runInSandbox}
            onChange={(runInSandbox) => update({ runInSandbox })}
            label={tr('ui.agent.foldersTab.runCodeInTheSandbox')}
            detail={tr('ui.agent.foldersTab.newChatsRunTheAgent')}
          />
        </div>
        <p className="mt-3.5 text-[13px] text-fg-dim">
          {tr('ui.agent.foldersTab.chatsKeepTheirOwnCopy')}
        </p>
      </Card>

      <Card>
        <Eyebrow>{tr('ui.agent.foldersTab.whatTheAgentIsTold')}</Eyebrow>
        <pre className="mt-3 whitespace-pre-wrap font-mono text-[12.5px] leading-relaxed text-fg">{folderPrompt(folders)}</pre>
      </Card>
    </div>
  );
}
