import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import { pickFolder } from '@/lib/folders';
import {
  askServer, askedPairing, changePcLink, findServers, finishAskedPairing, forgetAskedPairing, getPcLink, makePcLink,
  previewPcLink, removePcLink,
  type DeviceFolder, type DevicePower, type FoundServer, type PcLink,
} from '@/lib/devices';
import { Group, SettingRow, StatusPill, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { Toggle } from '@/pages/Notifications';
import { SentTasks } from '@/components/settings/SentTasks';
import { tr } from '@/lib/i18n';
import { cn } from '@/lib/utils';

const inputClass = 'h-10 w-full rounded-lg border border-line bg-panel-2 px-3 text-[13px] outline-none focus:border-accent md:h-9';

/** Folders with add (native picker), read-only switch and remove. */
function FolderList({ folders, onChange, disabled }: { folders: DeviceFolder[]; onChange: (f: DeviceFolder[]) => void; disabled?: boolean }) {
  const add = async () => {
    const path = await pickFolder();
    if (path && !folders.some((f) => f.path === path)) onChange([...folders, { path, writable: true }]);
  };
  return (
    <div>
      {folders.length ? (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {folders.map((f, i) => (
            <li key={f.path} className="flex flex-wrap items-center gap-2 px-2.5 py-2">
              <span className="min-w-0 flex-1 break-all font-mono text-[12px]">{f.path}</span>
              <label className="flex flex-none items-center gap-1.5 text-[12px] text-fg-dim">
                <input
                  type="checkbox"
                  checked={!f.writable}
                  disabled={disabled}
                  onChange={(e) => onChange(folders.map((x, j) => (j === i ? { ...x, writable: !e.target.checked } : x)))}
                />
                {tr('ui.thisPc.readOnly')}
              </label>
              <button type="button" disabled={disabled} onClick={() => onChange(folders.filter((_, j) => j !== i))} className={quietButtonClass}>{tr('ui.thisPc.remove')}</button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[12.5px] text-fg-dim">{tr('ui.thisPc.noFoldersAgentsCanT')}</p>
      )}
      <button type="button" disabled={disabled} onClick={() => void add()} className={cn(buttonClass, 'mt-2')}>{tr('ui.thisPc.addAFolder')}</button>
    </div>
  );
}

function PowerToggles({ powers, onChange, disabled }: { powers: DevicePower[]; onChange: (p: DevicePower[]) => void; disabled?: boolean }) {
  const set = (power: DevicePower, on: boolean) =>
    onChange(on ? Array.from(new Set([...powers, power])) : powers.filter((p) => p !== power));
  return (
    <Group>
      <SettingRow label={tr('ui.thisPc.files')} detail={tr('ui.thisPc.readWriteAndSearchFiles')}>
        <Toggle label={tr('ui.thisPc.files')} checked={powers.includes('files')} disabled={disabled} onChange={(v) => set('files', v)} />
      </SettingRow>
      <SettingRow label={tr('ui.thisPc.commands')} detail={tr('ui.thisPc.commandsRunAsYou')}>
        <Toggle label={tr('ui.thisPc.commands')} checked={powers.includes('commands')} disabled={disabled} onChange={(v) => set('commands', v)} />
      </SettingRow>
      <SettingRow label={tr('ui.thisPc.apps')} detail={tr('ui.thisPc.appsDetail')}>
        <Toggle label={tr('ui.thisPc.apps')} checked={powers.includes('apps')} disabled={disabled} onChange={(v) => set('apps', v)} />
      </SettingRow>
      <SettingRow label={tr('ui.thisPc.sendTasks')} detail={tr('ui.thisPc.sendTasksDetail')}>
        <Toggle label={tr('ui.thisPc.sendTasks')} checked={powers.includes('tasks')} disabled={disabled} onChange={(v) => set('tasks', v)} />
      </SettingRow>
    </Group>
  );
}

/** Step 1: the web address and the code. Step 2: whose account it is, and what to allow. */
function LinkForm({ defaults, onLinked }: { defaults: PcLink; onLinked: (l: PcLink) => void }) {
  // Opened from the web's "Open in the desktop app" (cognitrix://pair link).
  const [params] = useSearchParams();
  const [server, setServer] = useState(params.get('server') || defaults.server || '');
  const [code, setCode] = useState((params.get('code') || '').toUpperCase().slice(0, 12));
  const [account, setAccount] = useState('');
  const [name, setName] = useState(defaults.name || defaults.default_name || '');
  const [folders, setFolders] = useState<DeviceFolder[]>([]);
  const [powers, setPowers] = useState<DevicePower[]>(['files', 'commands']);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Tailscale: servers found on this PC's tailnet, and a request to pair without a code.
  const [found, setFound] = useState<FoundServer[] | null>(null);
  const [finding, setFinding] = useState(false);
  const [asked, setAsked] = useState<'' | 'pending' | 'approved'>('');
  const [askedAccount, setAskedAccount] = useState('');

  const find = async () => {
    setFinding(true);
    setError('');
    try {
      setFound(await findServers());
    } catch (err) {
      setFound(null);
      setError(errorMessage(err, tr('ui.thisPc.couldNotLookOnYourTailnet')));
    } finally {
      setFinding(false);
    }
  };

  const ask = async (address: string) => {
    setBusy(true);
    setError('');
    try {
      const view = await askServer(address, name.trim());
      setServer(view.server);
      setAskedAccount(view.account);
      setAsked('pending');
    } catch (err) {
      setError(errorMessage(err, tr('ui.thisPc.couldNotAskThatServer')));
    } finally {
      setBusy(false);
    }
  };

  // A request made before this screen was opened is still waiting in the engine.
  useEffect(() => {
    let alive = true;
    askedPairing().then((view) => {
      if (!alive || (view.status !== 'pending' && view.status !== 'approved')) return;
      setServer(view.server);
      setAskedAccount(view.account);
      setAsked(view.status);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Waiting for the yes on the web.
  useEffect(() => {
    if (asked !== 'pending') return;
    let alive = true;
    const t = setInterval(() => {
      askedPairing().then((view) => {
        if (!alive) return;
        if (view.status === 'approved') {
          setAskedAccount(view.account);
          setAsked('approved');
        } else if (view.status !== 'pending') {
          setAsked('');
          setError(view.status === 'denied' ? tr('ui.thisPc.itWasDeclinedOnTheWeb') : tr('ui.thisPc.nobodyApprovedItInTime'));
        }
      }).catch(() => {});
    }, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [asked]);

  const cancelAsk = () => {
    void forgetAskedPairing().catch(() => {});
    setAsked('');
    setAskedAccount('');
  };

  const check = async () => {
    setBusy(true);
    setError('');
    try {
      setAccount((await previewPcLink(server.trim(), code.trim())).account);
    } catch (err) {
      setError(errorMessage(err, tr('ui.thisPc.couldNotCheckThatCode')));
    } finally {
      setBusy(false);
    }
  };

  const allow = async () => {
    setBusy(true);
    setError('');
    try {
      onLinked(asked === 'approved'
        ? await finishAskedPairing({ name: name.trim(), folders, powers })
        : await makePcLink({ server: server.trim(), code: code.trim(), name: name.trim(), folders, powers }));
    } catch (err) {
      setError(errorMessage(err, tr('ui.thisPc.couldNotLinkThisPc')));
    } finally {
      setBusy(false);
    }
  };

  if (asked === 'pending') {
    return (
      <div role="status" className="space-y-3 rounded-[12px] border border-accent/40 bg-panel p-3.5">
        <div className="text-[14px] font-semibold">{tr('ui.thisPc.approveThisPcOnTheWeb')}</div>
        <p className="text-[12.5px] leading-snug text-fg-dim">{tr('ui.thisPc.approveThisPcDetail', { account: askedAccount, server })}</p>
        <button type="button" onClick={cancelAsk} className={quietButtonClass}>{tr('ui.thisPc.cancel')}</button>
      </div>
    );
  }

  const shownAccount = asked === 'approved' ? askedAccount : account;
  if (!shownAccount) {
    return (
      <div className="space-y-3 rounded-[12px] border border-line bg-panel p-3.5">
        <p className="text-[12.5px] leading-snug text-fg-dim">{tr('ui.thisPc.onTheWebGetACode')}</p>
        <div>
          <label htmlFor="link-server" className="mb-1 block text-[12px] text-fg-dim">{tr('ui.thisPc.webAddress')}</label>
          <input id="link-server" value={server} onChange={(e) => setServer(e.target.value)} placeholder="https://cognitrix.example.com" className={inputClass} />
          <button type="button" onClick={() => void find()} disabled={finding || busy} className={cn(quietButtonClass, 'mt-1.5 -ml-2.5')}>
            {finding ? tr('ui.thisPc.lookingOnYourTailnet') : tr('ui.thisPc.findItOnMyTailnet')}
          </button>
          {found && (found.length ? (
            <ul aria-label={tr('ui.thisPc.serversOnYourTailnet')} className="mt-1 divide-y divide-line rounded-lg border border-line">
              {found.map((f) => (
                <li key={f.server} className="flex flex-wrap items-center gap-2 px-2.5 py-2">
                  <span className="min-w-0 flex-1 basis-[160px]">
                    <span className="block truncate text-[13px] font-medium">{f.name}</span>
                    <span className="block break-all font-mono text-[11.5px] text-fg-dim">{f.server}</span>
                  </span>
                  <button type="button" disabled={busy} onClick={() => setServer(f.server)} className={buttonClass}>{tr('ui.thisPc.useThisAddress')}</button>
                  {f.ask_to_pair && (
                    <button type="button" disabled={busy} onClick={() => void ask(f.server)} className={primaryButtonClass}>{tr('ui.thisPc.pairWithoutACode')}</button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p role="status" className="mt-1 text-[12.5px] leading-snug text-fg-dim">{tr('ui.thisPc.noServerOnYourTailnet')}</p>
          ))}
        </div>
        <div>
          <label htmlFor="link-code" className="mb-1 block text-[12px] text-fg-dim">{tr('ui.thisPc.code')}</label>
          <input id="link-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="ABCD-EFGH" maxLength={12} className={cn(inputClass, 'font-mono tracking-[0.1em]')} />
        </div>
        {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
        <button type="button" onClick={() => void check()} disabled={busy || !server.trim() || code.replace(/[^A-Za-z0-9]/g, '').length < 8} className={primaryButtonClass}>{tr('ui.thisPc.continue')}</button>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-[12px] border border-accent/40 bg-panel p-3.5">
      <div>
        <div className="text-[14px] font-semibold">{tr('ui.thisPc.letAccountUseThisPc', { account: shownAccount })}</div>
        <p className="mt-1 text-[12.5px] leading-snug text-fg-dim">{tr('ui.thisPc.agentsInThatAccountWill')}</p>
      </div>
      <div>
        <label htmlFor="link-name" className="mb-1 block text-[12px] text-fg-dim">{tr('ui.thisPc.nameForThisPc')}</label>
        <input id="link-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} className={inputClass} />
      </div>
      <div>
        <div className="mb-1 text-[12px] text-fg-dim">{tr('ui.thisPc.foldersAgentsCanUse')}</div>
        <FolderList folders={folders} onChange={setFolders} disabled={busy} />
      </div>
      <PowerToggles powers={powers} onChange={setPowers} disabled={busy} />
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" onClick={() => { setAccount(''); setCode(''); if (asked) cancelAsk(); }} disabled={busy} className={quietButtonClass}>{tr('ui.thisPc.cancel')}</button>
        <button type="button" onClick={() => void allow()} disabled={busy || folders.length === 0} className={primaryButtonClass}>{tr('ui.thisPc.allow')}</button>
      </div>
    </div>
  );
}

/** Desktop app, Settings › Computers: link this PC to a web account, then manage the link. */
export function ThisPcLink() {
  const [link, setLink] = useState<PcLink | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmUnlink, setConfirmUnlink] = useState(false);

  const load = useCallback(() => {
    getPcLink().then(setLink).catch((err) => setError(errorMessage(err, tr('ui.thisPc.couldNotLoadThisPc'))));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const change = async (patch: Parameters<typeof changePcLink>[0]) => {
    setBusy(true);
    setError('');
    try {
      setLink(await changePcLink(patch));
    } catch (err) {
      setError(errorMessage(err, tr('ui.thisPc.couldNotSaveThat')));
    } finally {
      setBusy(false);
    }
  };

  const unlink = async () => {
    setBusy(true);
    try {
      setLink(await removePcLink());
      setConfirmUnlink(false);
    } catch (err) {
      setError(errorMessage(err, tr('ui.thisPc.couldNotUnlinkThisPc')));
    } finally {
      setBusy(false);
    }
  };

  if (!link) return <p role={error ? 'alert' : 'status'} className="text-[13px] text-fg-dim">{error || tr('ui.thisPc.loading')}</p>;
  if (!link.linked) return <LinkForm defaults={link} onLinked={setLink} />;

  const status = link.paused
    ? <StatusPill tone="warn">{tr('ui.thisPc.paused')}</StatusPill>
    : link.status === 'connected'
      ? <StatusPill tone="ok">{tr('ui.thisPc.connected')}</StatusPill>
      : <StatusPill tone="off">{link.status === 'connecting' ? tr('ui.thisPc.connecting') : tr('ui.thisPc.notConnected')}</StatusPill>;

  return (
    <div className="space-y-3">
      <Group>
        <SettingRow
          label={<span className="flex flex-wrap items-center gap-2">{link.name}{status}</span>}
          detail={<>{tr('ui.thisPc.linkedToAccountOnServer', { account: link.account, server: link.server })}{link.token_kept_in ? <span className="block text-fg-dim">{tr('ui.thisPc.tokenKeptIn', { place: link.token_kept_in })}</span> : null}{link.status === 'connected' && link.connected_to && (link.servers?.length ?? 0) > 1 ? <span className="block text-fg-dim">{tr('ui.thisPc.connectedThrough', { address: link.connected_to })}</span> : null}{link.error && link.status !== 'connected' ? <span className="block text-warn-ink">{link.error}</span> : null}</>}
        >
          <button type="button" onClick={() => void change({ paused: !link.paused })} disabled={busy} className={buttonClass}>
            {link.paused ? tr('ui.thisPc.resumeAgents') : tr('ui.thisPc.pauseAgents')}
          </button>
        </SettingRow>
      </Group>
      <div>
        <div className="mb-1 text-[12px] text-fg-dim">{tr('ui.thisPc.foldersAgentsCanUse')}</div>
        <FolderList folders={link.folders} onChange={(folders) => void change({ folders })} disabled={busy} />
      </div>
      <PowerToggles powers={link.powers} onChange={(powers) => void change({ powers })} disabled={busy} />
      <p className="text-[12px] leading-snug text-fg-dim">{tr('ui.thisPc.everyCommandAsksYouFirst')}</p>
      {link.powers.includes('tasks') && (
        <div>
          <div className="mb-1 mt-2 text-[12px] text-fg-dim">{tr('ui.thisPc.tasksSentToAccount', { account: link.account })}</div>
          <SentTasks account={link.account} />
        </div>
      )}
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {confirmUnlink ? (
          <>
            <span className="flex-1 text-[12.5px]">{tr('ui.thisPc.unlinkAgentsCanNoLonger')}</span>
            <button type="button" onClick={() => setConfirmUnlink(false)} className={quietButtonClass}>{tr('ui.thisPc.cancel')}</button>
            <button type="button" onClick={() => void unlink()} disabled={busy} className={cn(buttonClass, 'border-danger/40 text-danger-ink')}>{tr('ui.thisPc.unlink')}</button>
          </>
        ) : (
          <button type="button" onClick={() => setConfirmUnlink(true)} className={cn(quietButtonClass, 'text-danger-ink')}>{tr('ui.thisPc.unlinkThisPc')}</button>
        )}
      </div>
    </div>
  );
}
