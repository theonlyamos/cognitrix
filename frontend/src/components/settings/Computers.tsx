import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import {
  listDevices, makePairCode, pairCodeStatus, platformLabel, renameDevice, unpairDevice,
  type PairCode, type PairedDevice,
} from '@/lib/devices';
import { Group, SettingRow, StatusPill, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { tr, trn } from '@/lib/i18n';
import { PcScreen } from '@/components/PcScreen';
import { cn } from '@/lib/utils';

const inputClass = 'h-10 w-full rounded-lg border border-line bg-panel-2 px-3 text-[13px] outline-none focus:border-accent md:h-9';

const PcIcon = () => (
  <svg aria-hidden width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="flex-none text-fg-dim">
    <rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" />
  </svg>
);

function powersText(d: PairedDevice) {
  const names = [
    d.powers.includes('files') && tr('ui.computers.files'),
    d.powers.includes('commands') && tr('ui.computers.commands'),
    d.powers.includes('apps') && tr('ui.computers.apps'),
  ].filter(Boolean);
  return names.length ? names.join(', ') : tr('ui.computers.nothingAllowed');
}

/** One paired PC: status, shared folders, rename and unpair. */
function DeviceRow({ device, onChange, onRemove }: { device: PairedDevice; onChange: (d: PairedDevice) => void; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(device.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmUnpair, setConfirmUnpair] = useState(false);
  const [watching, setWatching] = useState(false);
  const kind = platformLabel(device.platform);

  const save = async () => {
    if (!name.trim() || name.trim() === device.name) return;
    setBusy(true);
    setError('');
    try {
      onChange(await renameDevice(device.id, name.trim()));
    } catch (err) {
      setError(errorMessage(err, tr('ui.computers.couldNotRenameThePc')));
    } finally {
      setBusy(false);
    }
  };

  const unpair = async () => {
    setBusy(true);
    setError('');
    try {
      await unpairDevice(device.id);
      onRemove();
    } catch (err) {
      setError(errorMessage(err, tr('ui.computers.couldNotUnpairThePc')));
      setBusy(false);
    }
  };

  return (
    <div className="px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <PcIcon />
        <div className="min-w-0 flex-1 basis-[180px]">
          <div className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium">
            <span className="truncate">{device.name}</span>
            {device.paused
              ? <StatusPill tone="warn">{tr('ui.computers.paused')}</StatusPill>
              : device.online
                ? <StatusPill tone="ok">{tr('ui.computers.online')}</StatusPill>
                : <StatusPill tone="off">{tr('ui.computers.offline')}</StatusPill>}
          </div>
          <div className="mt-0.5 text-[12px] leading-snug text-fg-dim">
            {[kind, powersText(device), trn(device.folders.length, 'ui.computers.oneFolder', 'ui.computers.nFolders')].filter(Boolean).join(' · ')}
          </div>
        </div>
        <div className="flex flex-none items-center gap-2">
          {device.powers.includes('apps') && device.online && !device.paused && (
            <button type="button" onClick={() => setWatching(true)} className={quietButtonClass}>{tr('ui.computers.viewScreen')}</button>
          )}
          <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className={buttonClass}>
            {open ? tr('ui.computers.done') : tr('ui.computers.manage')}
          </button>
        </div>
      </div>
      {watching && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-3 sm:p-6" onClick={(e) => { if (e.target === e.currentTarget) setWatching(false); }}>
          <div role="dialog" aria-modal="true" aria-label={tr('ui.computers.nameSScreen', { name: device.name })} className="flex max-h-full w-full max-w-[1100px] flex-col rounded-2xl border border-line bg-panel p-3 shadow-lg" onKeyDown={(e) => { if (e.key === 'Escape') setWatching(false); }}>
            <div className="mb-2 flex items-center gap-2">
              <span className="flex-1 truncate text-[13.5px] font-medium">{tr('ui.computers.nameSScreen', { name: device.name })}</span>
              <button type="button" autoFocus onClick={() => setWatching(false)} className={buttonClass}>{tr('ui.computers.close')}</button>
            </div>
            <div className="min-h-0 overflow-auto"><PcScreen device={device} every={1500} /></div>
          </div>
        </div>
      )}
      {open && (
        <div className="mt-3 space-y-3 rounded-lg border border-line bg-panel-2/50 p-3">
          <div>
            <label className="mb-1 block text-[12px] text-fg-dim" htmlFor={`pc-name-${device.id}`}>{tr('ui.computers.name')}</label>
            <div className="flex gap-2">
              <input id={`pc-name-${device.id}`} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} className={inputClass} />
              <button type="button" onClick={() => void save()} disabled={busy || !name.trim() || name.trim() === device.name} className={buttonClass}>{tr('ui.computers.save')}</button>
            </div>
          </div>
          <div>
            <div className="mb-1 text-[12px] text-fg-dim">{tr('ui.computers.foldersSharedOnThisPc')}</div>
            {device.folders.length ? (
              <ul className="space-y-1">
                {device.folders.map((f) => (
                  <li key={f.path} className="flex items-center gap-2 text-[12.5px]">
                    <span className="min-w-0 flex-1 break-all font-mono">{f.path}</span>
                    <span className="flex-none text-[11.5px] text-fg-dim">{f.writable ? tr('ui.computers.readWrite') : tr('ui.computers.readOnly')}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[12.5px] text-fg-dim">{tr('ui.computers.noFoldersYet')}</p>
            )}
            <p className="mt-1.5 text-[11.5px] leading-snug text-fg-dim">{tr('ui.computers.foldersAndPermissionsAreChanged')}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
            {confirmUnpair ? (
              <>
                <span className="flex-1 text-[12.5px]">{tr('ui.computers.unpairNameAgentsLose', { name: device.name })}</span>
                <button type="button" onClick={() => setConfirmUnpair(false)} className={quietButtonClass}>{tr('ui.computers.cancel')}</button>
                <button type="button" onClick={() => void unpair()} disabled={busy} className={cn(buttonClass, 'border-danger/40 text-danger-ink')}>{tr('ui.computers.unpair')}</button>
              </>
            ) : (
              <button type="button" onClick={() => setConfirmUnpair(true)} className={cn(quietButtonClass, 'text-danger-ink')}>{tr('ui.computers.unpairThisPc')}</button>
            )}
          </div>
          {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
        </div>
      )}
    </div>
  );
}

/** The pairing code, with the ways to use it, until a PC uses it. */
export function PairDialog({ onClose, onPaired }: { onClose: () => void; onPaired: (d: PairedDevice | null) => void }) {
  const [code, setCode] = useState<PairCode | null>(null);
  const [left, setLeft] = useState(0);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');
  const [expired, setExpired] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);

  const fresh = useCallback(() => {
    setError('');
    setExpired(false);
    makePairCode()
      .then((c) => { setCode(c); setLeft(c.expires_in); })
      .catch((err) => setError(errorMessage(err, tr('ui.computers.couldNotMakeAPairing'))));
  }, []);

  useEffect(() => { fresh(); }, [fresh]);
  useEffect(() => { dialogRef.current?.focus(); }, []);

  // Count down, and look every 2 seconds for the PC to use the code.
  useEffect(() => {
    if (!code || expired) return;
    let alive = true;
    const tick = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    const poll = setInterval(() => {
      pairCodeStatus(code.code).then((s) => {
        if (!alive) return;
        if (s.status === 'paired') onPaired(s.device ?? null);
        else if (s.status === 'expired') setExpired(true);
      }).catch(() => {});
    }, 2000);
    return () => { alive = false; clearInterval(tick); clearInterval(poll); };
  }, [code, expired, onPaired]);

  useEffect(() => { if (code && left === 0) setExpired(true); }, [code, left]);

  const copy = (text: string, what: string) => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(what);
      setTimeout(() => setCopied(''), 1500);
    }).catch(() => {});
  };

  const command = code ? `cognitrix connect ${code.server} --code ${code.code}` : '';
  const minutes = Math.floor(left / 60);
  const seconds = String(left % 60).padStart(2, '0');

  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={tr('ui.computers.pairAComputer')}
        tabIndex={-1}
        onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
        className="w-full max-w-[460px] rounded-t-2xl border border-line bg-panel p-5 shadow-lg outline-none sm:rounded-2xl"
      >
        <h2 className="text-[16px] font-semibold">{tr('ui.computers.pairAComputer')}</h2>
        <p className="mt-1 text-[13px] leading-snug text-fg-dim">
          {tr('ui.computers.onThePcYouWant')}
        </p>
        <div className="mt-4 rounded-xl border border-line bg-panel-2 px-4 py-4 text-center">
          {error ? (
            <p role="alert" className="text-[13px] text-danger-ink">{error}</p>
          ) : !code ? (
            <p role="status" className="text-[13px] text-fg-dim">{tr('ui.computers.makingACode')}</p>
          ) : (
            <>
              <div className={cn('font-mono text-[28px] font-semibold tracking-[0.12em]', expired && 'text-fg-dim line-through')} aria-label={tr('ui.computers.pairingCode')}>{code.code}</div>
              <div className="mt-1 text-[12px] text-fg-dim" role="status">
                {expired ? tr('ui.computers.thisCodeHasExpired') : tr('ui.computers.waitingForYourPcCode', { time: `${minutes}:${seconds}` })}
              </div>
              <div className="mt-3 flex justify-center gap-2">
                {expired
                  ? <button type="button" onClick={fresh} className={primaryButtonClass}>{tr('ui.computers.makeANewCode')}</button>
                  : <button type="button" onClick={() => copy(code.code, 'code')} className={buttonClass}>{copied === 'code' ? tr('ui.computers.copied') : tr('ui.computers.copyCode')}</button>}
              </div>
            </>
          )}
        </div>
        <ol className="mt-4 space-y-2.5 text-[13px] leading-snug">
          <li><span className="font-medium">{tr('ui.computers.withTheDesktopApp')}</span>{' '}<span className="text-fg-dim">{tr('ui.computers.openSettingsComputersLinkThis')}</span></li>
          <li>
            <span className="font-medium">{tr('ui.computers.orInATerminal')}</span>
            {code && (
              <div className="mt-1.5 flex items-center gap-2 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5">
                <code className="min-w-0 flex-1 break-all font-mono text-[11.5px]">{command}</code>
                <button type="button" onClick={() => copy(command, 'cmd')} className={quietButtonClass}>{copied === 'cmd' ? tr('ui.computers.copied') : tr('ui.computers.copy')}</button>
              </div>
            )}
          </li>
        </ol>
        <p className="mt-4 text-[12px] leading-snug text-fg-dim">{tr('ui.computers.thePcAsksYouTo')}</p>
        <div className="mt-4 flex justify-end">
          <button type="button" onClick={onClose} className={buttonClass}>{tr('ui.computers.close')}</button>
        </div>
      </div>
    </div>
  );
}

/** Settings › Computers: the sandbox, and the person's own paired PCs. */
export function ComputersSettings({ sandboxHref = '/settings/access#computer-sandbox' }: { sandboxHref?: string }) {
  const [devices, setDevices] = useState<PairedDevice[] | null>(null);
  const [error, setError] = useState('');
  const [pairing, setPairing] = useState(false);
  const [justPaired, setJustPaired] = useState('');

  const load = useCallback(() => {
    listDevices().then(setDevices).catch((err) => setError(errorMessage(err, tr('ui.computers.couldNotLoadYourPcs'))));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const onPaired = useCallback((d: PairedDevice | null) => {
    setPairing(false);
    if (d) setJustPaired(d.name);
    load();
  }, [load]);

  return (
    <>
      <Group>
        <SettingRow
          label={tr('ui.computers.sandboxComputer')}
          detail={tr('ui.computers.agentsUseTheirSandboxComputer')}
        >
          <Link to={sandboxHref} className={quietButtonClass}>{tr('ui.computers.sandboxSettings')}</Link>
        </SettingRow>
      </Group>

      <div className="mb-2 mt-7 flex items-center justify-between gap-3">
        <h3 className="font-mono text-[11px] font-medium uppercase tracking-[0.08em] text-fg-dim">{tr('ui.computers.yourPcs')}</h3>
        <button type="button" onClick={() => setPairing(true)} className={primaryButtonClass}>{tr('ui.computers.pairAComputer')}</button>
      </div>
      {justPaired && (
        <p role="status" className="mb-2 rounded-lg border border-ok/30 bg-ok/10 px-3 py-2 text-[12.5px]">
          {tr('ui.computers.nameIsPairedPickIt', { name: justPaired })}
        </p>
      )}
      {error && !devices && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
      {!devices && !error && <p role="status" className="text-[13px] text-fg-dim">{tr('ui.computers.loading')}</p>}
      {devices && devices.length === 0 && (
        <div className="rounded-[12px] border border-dashed border-line px-4 py-6 text-center">
          <p className="text-[13.5px] font-medium">{tr('ui.computers.noPcsPairedYet')}</p>
          <p className="mx-auto mt-1 max-w-[420px] text-[12.5px] leading-snug text-fg-dim">{tr('ui.computers.pairYourOwnComputerSo')}</p>
        </div>
      )}
      {devices && devices.length > 0 && (
        <Group>
          {devices.map((d) => (
            <DeviceRow
              key={d.id}
              device={d}
              onChange={(next) => setDevices((all) => (all || []).map((x) => (x.id === next.id ? next : x)))}
              onRemove={() => setDevices((all) => (all || []).filter((x) => x.id !== d.id))}
            />
          ))}
        </Group>
      )}
      {pairing && <PairDialog onClose={() => setPairing(false)} onPaired={onPaired} />}
    </>
  );
}
