import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import qrcode from 'qrcode-generator';
import { Laptop, Monitor, Smartphone, SquareTerminal } from 'lucide-react';
import { errorMessage } from '@/lib/api';
import { useUser } from '@/context/AppContext';
import {
  cancelSignInCode, endSignIn, listSignIns, makeSignInCode, signInCodeStatus, signInLink, signOutOthers,
  type SignInCode, type SignInDevice, type SignInList,
} from '@/lib/signIns';
import { timeAgo } from '@/lib/agentDraft';
import { Group, SettingRow, StatusPill, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';
import { cn } from '@/lib/utils';

function DeviceIcon({ device }: { device: SignInDevice }) {
  const Icon = device.kind === 'desktop' ? Monitor
    : device.kind === 'cli' ? SquareTerminal
      : /iPhone|Android|iPad/.test(device.name) ? Smartphone : Laptop;
  return (
    <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-panel-2 text-fg-dim">
      <Icon size={17} aria-hidden />
    </span>
  );
}

function methodText(d: SignInDevice): string {
  switch (d.method) {
    case 'password': return tr('ui.signIns.signedInWithPassword');
    case 'google': return tr('ui.signIns.signedInWithGoogle');
    case 'github': return tr('ui.signIns.signedInWithGithub');
    case 'code': return d.linked_pc
      ? tr('ui.signIns.linkedWithACodeFrom', { name: d.approved_by || tr('ui.signIns.anotherDevice') })
      : tr('ui.signIns.signedInWithACodeFrom', { name: d.approved_by || tr('ui.signIns.anotherDevice') });
    case 'pairing': return tr('ui.signIns.linkedWithAPairingCode');
    case 'earlier': return tr('ui.signIns.signedInBeforeThisList');
    default: return '';
  }
}

/** The QR code a phone camera scans to open the sign-in page with the code filled in. */
function QrCode({ text, size = 148 }: { text: string; size?: number }) {
  const svg = useMemo(() => {
    const qr = qrcode(0, 'M');
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  }, [text]);
  return (
    <div
      role="img"
      aria-label={tr('ui.signIns.qrCodeForThisSignIn')}
      className="flex-none rounded-lg bg-white p-1.5 [&_svg]:block [&_svg]:h-full [&_svg]:w-full"
      style={{ width: size, height: size }}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

/** "Sign in another device": a one-time code (and QR code) to type or scan on the new device. */
export function SignInCodeDialog({ onClose, onSignedIn }: { onClose: () => void; onSignedIn: () => void }) {
  const [code, setCode] = useState<SignInCode | null>(null);
  const [left, setLeft] = useState(0);
  const [error, setError] = useState('');
  const [expired, setExpired] = useState(false);
  const [copied, setCopied] = useState('');
  const [device, setDevice] = useState<SignInDevice | null>(null);
  const [ending, setEnding] = useState(false);
  const [ended, setEnded] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const live = useRef<string>('');

  const fresh = useCallback(() => {
    setError('');
    setExpired(false);
    setCode(null);
    makeSignInCode()
      .then((c) => { setCode(c); setLeft(c.expires_in); live.current = c.code; })
      .catch((err) => setError(errorMessage(err, tr('ui.signIns.couldNotMakeACode'))));
  }, []);

  useEffect(() => { fresh(); }, [fresh]);
  useEffect(() => { dialogRef.current?.focus(); }, []);

  // Count down, and look every 2 seconds for the new device to use the code.
  useEffect(() => {
    if (!code || expired || device) return;
    let alive = true;
    const tick = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    const poll = setInterval(() => {
      signInCodeStatus(code.code).then((s) => {
        if (!alive) return;
        if (s.status === 'used') {
          live.current = '';
          setDevice(s.sign_in ?? { id: '', name: tr('ui.signIns.aNewDevice'), kind: 'browser', method: 'code', approved_by: '', address: '', created_at: '', last_seen_at: '', current: false });
          onSignedIn();
        } else if (s.status === 'expired') setExpired(true);
      }).catch(() => {});
    }, 2000);
    return () => { alive = false; clearInterval(tick); clearInterval(poll); };
  }, [code, expired, device, onSignedIn]);

  useEffect(() => { if (code && left === 0 && !device) setExpired(true); }, [code, left, device]);

  const close = () => {
    if (live.current) void cancelSignInCode(live.current).catch(() => {});
    onClose();
  };

  const copy = (text: string, what: string) => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(what);
      setTimeout(() => setCopied(''), 1500);
    }).catch(() => {});
  };

  const signItOut = async () => {
    if (!device?.id) return;
    setEnding(true);
    try {
      await endSignIn(device.id);
      setEnded(true);
      onSignedIn();
    } catch (err) {
      setError(errorMessage(err, tr('ui.signIns.couldNotSignItOut')));
    } finally {
      setEnding(false);
    }
  };

  const command = code ? `cognitrix connect ${code.server || window.location.origin} --code ${code.code}` : '';
  const minutes = Math.floor(left / 60);
  const seconds = String(left % 60).padStart(2, '0');

  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4" onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={tr('ui.signIns.signInAnotherDevice')}
        tabIndex={-1}
        onKeyDown={(e) => { if (e.key === 'Escape') close(); }}
        className="w-full max-w-[480px] rounded-t-2xl border border-line bg-panel p-5 shadow-lg outline-none sm:rounded-2xl"
      >
        <h2 className="text-[16px] font-semibold">{device ? tr('ui.signIns.signedIn') : tr('ui.signIns.signInAnotherDevice')}</h2>
        {device ? (
          <>
            <div className="mt-4 flex items-center gap-3 rounded-xl border border-line bg-panel-2 px-4 py-3.5">
              <DeviceIcon device={device} />
              <div className="min-w-0 flex-1">
                <div className={cn('text-[14px] font-medium', ended && 'text-fg-dim line-through')}>{device.name}</div>
                <div className="text-[12px] text-fg-dim" role="status">
                  {ended ? tr('ui.signIns.signedOut') : device.linked_pc ? tr('ui.signIns.isNowLinked') : tr('ui.signIns.isNowSignedInAs')}
                </div>
              </div>
            </div>
            {error && <p role="alert" className="mt-3 text-[12.5px] text-danger-ink">{error}</p>}
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              {!ended && device.id && (
                <button type="button" onClick={signItOut} disabled={ending} className={cn(buttonClass, 'text-danger-ink')}>
                  {ending ? tr('ui.signIns.signingOut') : tr('ui.signIns.notMeSignItOut')}
                </button>
              )}
              <button type="button" onClick={onClose} className={primaryButtonClass}>{tr('ui.signIns.done')}</button>
            </div>
          </>
        ) : (
          <>
            <p className="mt-1 text-[13px] leading-snug text-fg-dim">{tr('ui.signIns.onTheNewDeviceOpen')}</p>
            <div className="mt-4 rounded-xl border border-line bg-panel-2 p-4">
              {error ? (
                <p role="alert" className="text-center text-[13px] text-danger-ink">{error}</p>
              ) : !code ? (
                <p role="status" className="text-center text-[13px] text-fg-dim">{tr('ui.signIns.makingACode')}</p>
              ) : (
                <div className="flex flex-col items-center gap-4 sm:flex-row">
                  <div className={cn(expired && 'opacity-30')}><QrCode text={signInLink(code.code)} /></div>
                  <div className="flex min-w-0 flex-1 flex-col items-center gap-2 sm:items-start">
                    <div className={cn('font-mono text-[26px] font-semibold tracking-[0.12em]', expired && 'text-fg-dim line-through')} aria-label={tr('ui.signIns.signInCode')}>{code.code}</div>
                    <div className="text-[12px] text-fg-dim" role="status">
                      {expired ? tr('ui.signIns.thisCodeHasExpired') : tr('ui.signIns.waitingWorksOnce', { time: `${minutes}:${seconds}` })}
                    </div>
                    {expired
                      ? <button type="button" onClick={fresh} className={primaryButtonClass}>{tr('ui.signIns.makeANewCode')}</button>
                      : (
                        <div className="flex flex-wrap gap-2">
                          <button type="button" onClick={() => copy(code.code, 'code')} className={buttonClass}>{copied === 'code' ? tr('ui.signIns.copied') : tr('ui.signIns.copyCode')}</button>
                          <button type="button" onClick={() => copy(signInLink(code.code), 'link')} className={buttonClass}>{copied === 'link' ? tr('ui.signIns.copied') : tr('ui.signIns.copyLink')}</button>
                        </div>
                      )}
                  </div>
                </div>
              )}
            </div>
            {code && !expired && (
              <div className="mt-3 text-[12.5px] leading-snug">
                <span className="font-medium">{tr('ui.signIns.onAPc')}</span>{' '}
                <span className="text-fg-dim">{tr('ui.signIns.inTheDesktopAppOpen')}</span>
                <div className="mt-1.5 flex items-center gap-2 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5">
                  <code className="min-w-0 flex-1 break-all font-mono text-[11.5px]">{command}</code>
                  <button type="button" onClick={() => copy(command, 'cmd')} className={quietButtonClass}>{copied === 'cmd' ? tr('ui.signIns.copied') : tr('ui.signIns.copy')}</button>
                </div>
              </div>
            )}
            <p className="mt-3 text-[12px] leading-snug text-fg-dim">{tr('ui.signIns.anyoneWhoHasThisCode')}</p>
            <div className="mt-4 flex justify-end">
              <button type="button" onClick={close} className={buttonClass}>{tr('ui.signIns.cancel')}</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** Settings › Sign-ins: the devices signed in to this account. */
export function SignInsSettings() {
  const { logout } = useUser();
  const [data, setData] = useState<SignInList | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    listSignIns().then((d) => { setData(d); setError(''); })
      .catch((err) => setError(errorMessage(err, tr('ui.signIns.couldNotLoadSignIns'))));
  }, []);
  useEffect(() => { load(); }, [load]);

  const end = async (device: SignInDevice) => {
    setBusy(device.id);
    try {
      const { was_current } = await endSignIn(device.id);
      if (was_current) { logout(); return; }
      load();
    } catch (err) {
      setError(errorMessage(err, tr('ui.signIns.couldNotSignItOut')));
    } finally {
      setBusy('');
    }
  };

  const endOthers = async () => {
    setBusy('others');
    try {
      await signOutOthers();
      load();
    } catch (err) {
      setError(errorMessage(err, tr('ui.signIns.couldNotSignOutOthers')));
    } finally {
      setBusy('');
    }
  };

  const others = (data?.sign_ins ?? []).filter((d) => !d.current);

  return (
    <>
      <Group>
        <SettingRow
          label={tr('ui.signIns.signInAnotherDevice')}
          detail={data && !data.code_sign_in ? tr('ui.signIns.turnedOffOnThisServer') : tr('ui.signIns.makeAOneTimeCode')}
          lead={<span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-panel-2 text-fg-dim"><QrGlyph /></span>}
        >
          <button type="button" onClick={() => setAdding(true)} disabled={!data?.code_sign_in} className={primaryButtonClass}>
            {tr('ui.signIns.getACode')}
          </button>
        </SettingRow>
      </Group>

      <div className="mb-2 mt-7 flex items-center justify-between gap-3">
        <SubHeading className="mb-0 mt-0">{tr('ui.signIns.signedIn')}{data ? ` · ${data.sign_ins.length}` : ''}</SubHeading>
        {others.length > 0 && (
          <button type="button" onClick={endOthers} disabled={busy === 'others'} className="text-[12.5px] font-medium text-danger-ink hover:underline disabled:opacity-60">
            {busy === 'others' ? tr('ui.signIns.signingOut') : tr('ui.signIns.signOutAllOthers')}
          </button>
        )}
      </div>
      {error && <p role="alert" className="mb-2 text-[12.5px] text-danger-ink">{error}</p>}
      {!data ? (
        !error && <p role="status" className="text-[13px] text-fg-dim">{tr('ui.signIns.loading')}</p>
      ) : (
        <Group>
          {data.sign_ins.map((d) => (
            <SettingRow
              key={d.id}
              lead={<DeviceIcon device={d} />}
              label={<span className="inline-flex flex-wrap items-center gap-2">{d.name}{d.current && <StatusPill tone="ok">{tr('ui.signIns.thisDevice')}</StatusPill>}</span>}
              detail={(
                <>
                  <span>{[d.address, d.current ? tr('ui.signIns.activeNow') : tr('ui.signIns.activeAgo', { ago: timeAgo(d.last_seen_at) })].filter(Boolean).join(' · ')}</span>
                  <br />
                  <span>{methodText(d)}</span>
                </>
              )}
            >
              {!d.current && (
                <button type="button" onClick={() => end(d)} disabled={busy === d.id} className={cn(buttonClass, 'border-transparent text-danger-ink')}>
                  {busy === d.id ? tr('ui.signIns.signingOut') : d.linked_pc ? tr('ui.signIns.unlink') : tr('ui.signIns.signOut')}
                </button>
              )}
            </SettingRow>
          ))}
          {!data.current_listed && (
            <p className="px-3.5 py-3 text-[12px] leading-snug text-fg-dim">{tr('ui.signIns.thisDeviceSignedInBefore')}</p>
          )}
          {data.sign_ins.length === 0 && data.current_listed && (
            <p className="px-3.5 py-3 text-[12.5px] text-fg-dim">{tr('ui.signIns.noDevices')}</p>
          )}
        </Group>
      )}
      {adding && <SignInCodeDialog onClose={() => { setAdding(false); load(); }} onSignedIn={load} />}
    </>
  );
}

function QrGlyph() {
  return (
    <svg aria-hidden width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><path d="M14 14h3v3M21 14v.01M14 21h3M21 18v3" />
    </svg>
  );
}
