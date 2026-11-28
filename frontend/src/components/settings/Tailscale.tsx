import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import qrcode from 'qrcode-generator';
import { errorMessage } from '@/lib/api';
import { isDesktop } from '@/lib/desktop';
import { tr } from '@/lib/i18n';
import {
  addressesKey, checkTailscale, connectTailscale, disconnectTailscale, loadAddresses, saveTailscale, tailscalePending,
  tailscaleStatus,
  type TailscaleAddress, type TailscaleAllow, type TailscaleChange, type TailscaleCheck, type TailscaleStatus,
} from '@/lib/tailscale';
import { Toggle } from '@/pages/Notifications';
import {
  Group, SectionHeader, SettingRow, StatusPill, SubHeading, buttonClass, primaryButtonClass, quietButtonClass,
} from '@/components/settings/ui';

const OPERATOR_COMMAND = 'sudo tailscale set --operator=$USER'; // i18n-ignore
const DOWNLOAD_URL = 'https://tailscale.com/download';
const DNS_URL = 'https://login.tailscale.com/admin/dns';

const fieldClass = 'h-9 w-full min-w-0 rounded-lg border border-line bg-bg px-2.5 text-[13px] outline-none focus:border-accent disabled:opacity-60 md:h-8';

function Qr({ text }: { text: string }) {
  const svg = useMemo(() => {
    const qr = qrcode(0, 'M');
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  }, [text]);
  return (
    <div
      role="img"
      aria-label={tr('ui.tailscale.qrToSignIn')}
      className="h-[132px] w-[132px] flex-none rounded-lg bg-white p-1.5 [&_svg]:block [&_svg]:h-full [&_svg]:w-full"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{children}</a>;
}

/** What stands between the owner and a working address, in words they can act on. */
function Problem({ address, kind }: { address: TailscaleAddress; kind: 'private' | 'public' }) {
  if (!address.wanted && address.problem === 'not_removed') {
    return (
      <div role="status" className="px-3.5 py-3 text-[12.5px] leading-snug text-warn-ink">
        {kind === 'public' ? tr('ui.tailscale.publicNotRemovedYet') : tr('ui.tailscale.privateNotRemovedYet')}
        {address.detail && <>{' '}{tr('ui.tailscale.tailscaleSaid', { detail: address.detail })}</>}
      </div>
    );
  }
  if (!address.wanted || address.active) return null;
  let body: ReactNode;
  switch (address.problem) {
    case 'needs_turning_on':
      body = (
        <>
          {kind === 'public' ? tr('ui.tailscale.funnelNeedsTurningOn') : tr('ui.tailscale.httpsNeedsTurningOn')}{' '}
          <ExternalLink href={address.needs_url}>{tr('ui.tailscale.turnItOnAtTailscale')}</ExternalLink>
        </>
      );
      break;
    case 'port_in_use':
      body = tr('ui.tailscale.portInUse', { port: address.port });
      break;
    case 'same_port':
      body = tr('ui.tailscale.samePort');
      break;
    case 'needs_operator':
      body = (
        <>
          {tr('ui.tailscale.needsOperator')}
          <code className="mt-1.5 block select-all rounded bg-bg px-2 py-1.5 font-mono text-[11.5px] text-fg">{OPERATOR_COMMAND}</code>
        </>
      );
      break;
    case 'needs_magicdns':
      body = (<>{tr('ui.tailscale.needsMagicDns')}{' '}<ExternalLink href={DNS_URL}>{tr('ui.tailscale.openDnsSettings')}</ExternalLink></>);
      break;
    case 'other':
      body = tr('ui.tailscale.tailscaleSaid', { detail: address.detail });
      break;
    default:
      body = tr('ui.tailscale.settingUp');
  }
  return <div role="status" className="px-3.5 py-3 text-[12.5px] leading-snug text-warn-ink">{body}</div>;
}

function AddressLine({ url, check }: { url: string; check?: { ok: boolean; detail: string } }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(url).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };
  return (
    <div className="flex flex-wrap items-center gap-2 px-3.5 py-3">
      <code className="min-w-0 flex-1 basis-[220px] break-all rounded bg-bg px-2 py-1.5 font-mono text-[12px]">{url}</code>
      {check && (
        <StatusPill tone={check.ok ? 'ok' : 'warn'}>
          {check.ok ? tr('ui.tailscale.reachable') : tr('ui.tailscale.notReachedFromHere')}
        </StatusPill>
      )}
      <button type="button" onClick={copy} className={buttonClass}>{copied ? tr('ui.tailscale.copied') : tr('ui.tailscale.copy')}</button>
    </div>
  );
}

function allowChoice(allow: TailscaleAllow[]): 'webhooks' | 'links' | 'app' {
  if (allow.includes('app')) return 'app';
  return allow.includes('links') ? 'links' : 'webhooks';
}

const ALLOW_FOR: Record<'webhooks' | 'links' | 'app', TailscaleAllow[]> = {
  webhooks: ['webhooks'], links: ['webhooks', 'links'], app: ['app'],
};

/** Sign in the Tailscale that Cognitrix runs itself (a server in Docker or on Fly). */
function SignIn({ status, onStatus, onError }: {
  status: TailscaleStatus;
  onStatus: (s: TailscaleStatus) => void;
  onError: (m: string) => void;
}) {
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const connect = async (authKey: string) => {
    setBusy(true);
    onError('');
    try {
      onStatus(await connectTailscale(authKey));
      setKey('');
    } catch (err) {
      onError(errorMessage(err, tr('ui.tailscale.couldNotSignIn')));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="px-3.5 py-3">
      {status.login_url ? (
        <div className="flex flex-wrap items-start gap-4">
          <Qr text={status.login_url} />
          <div className="min-w-0 flex-1 basis-[220px] text-[12.5px] leading-snug">
            <p className="font-medium text-fg">{tr('ui.tailscale.openThisPageToSignIn')}</p>
            <p className="mt-1 break-all"><ExternalLink href={status.login_url}>{status.login_url}</ExternalLink></p>
            <p className="mt-2 text-fg-dim">{tr('ui.tailscale.thisPageUpdatesItself')}</p>
          </div>
        </div>
      ) : (
        <button type="button" disabled={busy} onClick={() => void connect('')} className={primaryButtonClass}>
          {busy ? tr('ui.tailscale.startingTailscale') : tr('ui.tailscale.signInToTailscale')}
        </button>
      )}
      <form
        className="mt-3 flex flex-wrap items-center gap-2"
        onSubmit={(e) => { e.preventDefault(); if (key.trim()) void connect(key.trim()); }}
      >
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          autoComplete="off"
          aria-label={tr('ui.tailscale.joinKey')}
          placeholder={tr('ui.tailscale.orPasteAJoinKey')}
          className={`${fieldClass} flex-1 basis-[220px]`}
        />
        <button type="submit" disabled={busy || !key.trim()} className={buttonClass}>{tr('ui.tailscale.useKey')}</button>
      </form>
      <p className="mt-1.5 text-[12px] leading-snug text-fg-dim">{tr('ui.tailscale.joinKeyIsUsedOnce')}</p>
    </div>
  );
}

function stateText(s: TailscaleStatus): { tone: 'ok' | 'warn' | 'off'; pill: string; detail: ReactNode } {
  const own = s.mode !== 'builtin';
  switch (s.state) {
    case 'running':
      return {
        tone: 'ok', pill: tr('ui.tailscale.connected'),
        detail: [s.user ? tr('ui.tailscale.signedInAs', { user: s.user }) : (s.tags.length ? s.tags.join(', ') : ''), s.ips[0] ?? '']
          .filter(Boolean).join(' · '),
      };
    case 'missing':
      return {
        tone: 'off', pill: tr('ui.tailscale.notInstalled'),
        detail: (<>{tr('ui.tailscale.installTailscaleFirst')}{' '}<ExternalLink href={DOWNLOAD_URL}>{tr('ui.tailscale.getTailscale')}</ExternalLink></>),
      };
    case 'needs_login':
      return {
        tone: 'warn', pill: tr('ui.tailscale.signedOut'),
        detail: own ? tr('ui.tailscale.openTailscaleAndSignIn') : tr('ui.tailscale.signThisServerIn'),
      };
    case 'needs_approval':
      return { tone: 'warn', pill: tr('ui.tailscale.waitingForApproval'), detail: tr('ui.tailscale.anAdminOfYourTailnet') };
    case 'starting':
      return { tone: 'warn', pill: tr('ui.tailscale.starting'), detail: '' };
    default:
      return { tone: 'off', pill: tr('ui.tailscale.notRunning'), detail: tr('ui.tailscale.startTailscaleOnThisComputer') };
  }
}

function expiresSoon(iso: string): string {
  const when = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(when) || when - Date.now() > 14 * 86400_000) return '';
  return new Date(when).toLocaleDateString();
}

/** Settings › Tailscale: the private address, and the public address for webhooks. */
export function TailscaleSettings() {
  const [status, setStatus] = useState<TailscaleStatus | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<TailscaleCheck | null>(null);
  const [checking, setChecking] = useState(false);

  const load = useCallback(() => {
    tailscaleStatus().then(setStatus)
      .catch((err) => setError(errorMessage(err, tr('ui.tailscale.couldNotLoad'))));
  }, []);
  useEffect(() => { load(); }, [load]);

  // An address that comes up (or goes) after the page loaded, such as once the
  // owner turns Funnel on at Tailscale: everything that shows addresses asks again.
  const addresses = addressesKey(status);
  const lastAddresses = useRef<string | null>(null);
  useEffect(() => {
    if (!status) return;
    if (lastAddresses.current !== null && lastAddresses.current !== addresses) void loadAddresses(true);
    lastAddresses.current = addresses;
  }, [status, addresses]);

  // A sign-in or a feature being turned on happens elsewhere: keep looking.
  const pending = tailscalePending(status);
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [pending, load]);

  const save = async (change: TailscaleChange) => {
    setBusy(true);
    setError('');
    setCheck(null);
    try {
      setStatus(await saveTailscale(change));
      void loadAddresses(true);
    } catch (err) {
      setError(errorMessage(err, tr('ui.tailscale.couldNotSave')));
    } finally {
      setBusy(false);
    }
  };

  const runCheck = async () => {
    setChecking(true);
    try {
      setCheck(await checkTailscale());
    } catch (err) {
      setError(errorMessage(err, tr('ui.tailscale.couldNotCheck')));
    } finally {
      setChecking(false);
    }
  };

  const signOut = async () => {
    setBusy(true);
    try {
      setStatus(await disconnectTailscale());
    } catch (err) {
      setError(errorMessage(err, tr('ui.tailscale.couldNotSignOut')));
    } finally {
      setBusy(false);
    }
  };

  const header = <SectionHeader title={tr('ui.tailscale.tailscale')} description={tr('ui.tailscale.description')} />;
  if (!status) {
    return (
      <>
        {header}
        {error ? <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>
          : <p role="status" className="text-[13px] text-fg-dim">{tr('ui.tailscale.loading')}</p>}
      </>
    );
  }

  const locked = !status.can_edit || busy;
  const state = stateText(status);
  const running = status.enabled && status.state === 'running';
  const expiry = running ? expiresSoon(status.key_expiry) : '';
  const choice = allowChoice(status.public.allow);
  const anyActive = status.private.active || status.public.active;
  const elsewhere = status.private.active && typeof window !== 'undefined'
    && window.location.hostname !== status.name;

  return (
    <>
      {header}
      {error && <p role="alert" className="mb-2 text-[12.5px] text-danger-ink">{error}</p>}
      {!status.can_edit && <p className="mb-2 text-[12.5px] text-fg-dim">{tr('ui.tailscale.onlyAnAdminCanChange')}</p>}

      <Group>
        <SettingRow label={tr('ui.tailscale.useTailscale')} detail={tr('ui.tailscale.useTailscaleDetail')}>
          <Toggle label={tr('ui.tailscale.useTailscale')} checked={status.enabled} disabled={locked} onChange={(v) => void save({ enabled: v })} />
        </SettingRow>
        <SettingRow
          label={(
            <span className="inline-flex flex-wrap items-center gap-2">
              {status.state === 'running' && status.name ? status.name : tr('ui.tailscale.thisComputer')}
              <StatusPill tone={state.tone}>{state.pill}</StatusPill>
            </span>
          )}
          detail={state.detail}
        >
          {status.can_sign_in && status.state === 'running' && status.can_edit && (
            <button type="button" disabled={busy} onClick={() => void signOut()} className={quietButtonClass}>{tr('ui.tailscale.signOut')}</button>
          )}
        </SettingRow>
        {status.enabled && status.state === 'needs_login' && status.mode !== 'builtin' && status.login_url && (
          <div className="px-3.5 py-3 text-[12.5px]"><ExternalLink href={status.login_url}>{tr('ui.tailscale.openTheSignInPage')}</ExternalLink></div>
        )}
        {status.enabled && status.can_sign_in && status.state === 'needs_login' && status.can_edit && (
          <SignIn status={status} onStatus={setStatus} onError={setError} />
        )}
        {status.enabled && status.can_sign_in && status.state !== 'running' && status.can_edit && (
          <SettingRow label={tr('ui.tailscale.nameOnYourTailnet')} detail={tr('ui.tailscale.nameOnYourTailnetDetail')}>
            <input
              key={status.hostname}
              defaultValue={status.hostname}
              maxLength={63}
              disabled={locked}
              aria-label={tr('ui.tailscale.nameOnYourTailnet')}
              onBlur={(e) => { if (e.target.value.trim() && e.target.value.trim() !== status.hostname) void save({ hostname: e.target.value.trim() }); }}
              className={`${fieldClass} w-[180px]`}
            />
          </SettingRow>
        )}
        {expiry && (
          <div role="status" className="px-3.5 py-3 text-[12.5px] leading-snug text-warn-ink">{tr('ui.tailscale.signInEndsOn', { date: expiry })}</div>
        )}
      </Group>

      {running && (
        <>
          <SubHeading>{tr('ui.tailscale.privateAddress')}</SubHeading>
          <Group>
            <SettingRow label={tr('ui.tailscale.privateAddress')} detail={tr('ui.tailscale.privateAddressDetail')}>
              <Toggle label={tr('ui.tailscale.privateAddress')} checked={status.private.wanted} disabled={locked} onChange={(v) => void save({ private: v })} />
            </SettingRow>
            {status.private.active && <AddressLine url={status.private.url} check={check?.private} />}
            <Problem address={status.private} kind="private" />
            {status.private.active && (
              <p className="px-3.5 py-3 text-[12px] leading-snug text-fg-dim">
                {elsewhere ? tr('ui.tailscale.passkeysBelongToOneAddressOpenIt') : tr('ui.tailscale.passkeysBelongToOneAddress')}
              </p>
            )}
            {status.private.wanted && status.private.problem === 'port_in_use' && (
              <SettingRow label={tr('ui.tailscale.port')} detail={tr('ui.tailscale.privatePortDetail')}>
                <input
                  key={status.private.port}
                  type="number"
                  min={1}
                  max={65535}
                  defaultValue={status.private.port}
                  disabled={locked}
                  aria-label={tr('ui.tailscale.port')}
                  onBlur={(e) => { const n = Number(e.target.value); if (n && n !== status.private.port) void save({ private_port: n }); }}
                  className={`${fieldClass} w-[110px]`}
                />
              </SettingRow>
            )}
            {status.private.active && (
              <SettingRow label={tr('ui.tailscale.signInFromTheTailnet')} detail={tr('ui.tailscale.signInFromTheTailnetDetail')}>
                <Toggle label={tr('ui.tailscale.signInFromTheTailnet')} checked={status.signin} disabled={locked} onChange={(v) => void save({ signin: v })} />
              </SettingRow>
            )}
            {!isDesktop() && status.private.active && (
              <SettingRow label={tr('ui.tailscale.onlyThroughTailscale')} detail={tr('ui.tailscale.onlyThroughTailscaleDetail')}>
                <Toggle label={tr('ui.tailscale.onlyThroughTailscale')} checked={status.listen_local} disabled={locked} onChange={(v) => void save({ listen_local: v })} />
              </SettingRow>
            )}
          </Group>

          <SubHeading>{tr('ui.tailscale.publicAddress')}</SubHeading>
          <Group>
            <SettingRow
              label={tr('ui.tailscale.publicAddress')}
              detail={isDesktop() ? tr('ui.tailscale.publicAddressDetailDesktop') : tr('ui.tailscale.publicAddressDetail')}
            >
              <Toggle label={tr('ui.tailscale.publicAddress')} checked={status.public.wanted} disabled={locked} onChange={(v) => void save({ public: v })} />
            </SettingRow>
            {status.public.active && <AddressLine url={status.public.url} check={check?.public} />}
            <Problem address={status.public} kind="public" />
            {status.public.wanted && (
              <>
                <SettingRow label={tr('ui.tailscale.whatItLetsThrough')} detail={choice === 'app' ? tr('ui.tailscale.wholeAppWarning') : tr('ui.tailscale.everythingElseIsRefused')}>
                  <select
                    value={choice}
                    disabled={locked}
                    aria-label={tr('ui.tailscale.whatItLetsThrough')}
                    onChange={(e) => void save({ allow: ALLOW_FOR[e.target.value as 'webhooks' | 'links' | 'app'] })}
                    className={`${fieldClass} w-auto`}
                  >
                    <option value="webhooks">{tr('ui.tailscale.webhooksOnly')}</option>
                    <option value="links">{tr('ui.tailscale.webhooksAndPublicLinks')}</option>
                    <option value="app">{tr('ui.tailscale.theWholeApp')}</option>
                  </select>
                </SettingRow>
                <SettingRow label={tr('ui.tailscale.port')} detail={tr('ui.tailscale.publicPortDetail')}>
                  <select
                    value={status.public.port}
                    disabled={locked}
                    aria-label={tr('ui.tailscale.port')}
                    onChange={(e) => void save({ public_port: Number(e.target.value) })}
                    className={`${fieldClass} w-auto`}
                  >
                    {status.public.ports.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </SettingRow>
              </>
            )}
          </Group>

          {anyActive && status.can_edit && (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button type="button" disabled={checking} onClick={() => void runCheck()} className={buttonClass}>
                {checking ? tr('ui.tailscale.checking') : tr('ui.tailscale.checkTheAddresses')}
              </button>
              {check && Object.values(check).some((c) => c && !c.ok) && (
                <span className="text-[12px] leading-snug text-fg-dim">{tr('ui.tailscale.checkFromAnotherDevice')}</span>
              )}
            </div>
          )}
        </>
      )}
    </>
  );
}
