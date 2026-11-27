import { useCallback, useEffect, useState } from 'react';
import { Network } from 'lucide-react';
import { errorMessage } from '@/lib/api';
import { linkTailnet, tailnetLink, unlinkTailnet, useAddresses, type TailnetLink } from '@/lib/tailscale';
import { timeAgo } from '@/lib/agentDraft';
import { Group, SettingRow, StatusPill, SubHeading, buttonClass, primaryButtonClass } from '@/components/settings/ui';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

/** Settings › Sign-ins › Tailscale: link the Tailscale login this browser is seen as to my
 *  account, so opening Cognitrix from my tailnet signs me in. Shown only when an admin turned
 *  it on (Settings › Tailscale), or while a link from before still stands. */
export function TailnetSignIn() {
  const [data, setData] = useState<TailnetLink | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const addresses = useAddresses();

  const load = useCallback(() => {
    tailnetLink().then(setData).catch(() => setData(null));
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!data || (!data.enabled && !data.linked)) return null;

  const run = async (work: () => Promise<TailnetLink>, failed: string) => {
    setBusy(true);
    setError('');
    try {
      setData(await work());
      setConfirming(false);
    } catch (err) {
      setError(errorMessage(err, failed));
    } finally {
      setBusy(false);
    }
  };

  const seen = data.login;
  const canLink = data.enabled && !!seen && seen !== data.linked;
  // Not open at the Tailscale address: that is where Tailscale says who this is.
  const privateAddress = addresses?.private ? `${addresses.private.replace(/\/$/, '')}/settings/sign-ins` : '';

  return (
    <>
      <SubHeading>{tr('ui.settings.tailnetSignIn.tailscale')}</SubHeading>
      {error && <p role="alert" className="mb-2 text-[12.5px] text-danger-ink">{error}</p>}
      <Group>
        <SettingRow
          lead={<span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-panel-2 text-fg-dim"><Network size={17} aria-hidden /></span>}
          label={(
            <span className="inline-flex flex-wrap items-center gap-2">
              {tr('ui.settings.tailnetSignIn.signInFromYourTailnet')}
              {data.linked && data.enabled && <StatusPill tone="ok">{tr('ui.settings.tailnetSignIn.on')}</StatusPill>}
            </span>
          )}
          detail={data.linked ? (
            <>
              <span>{tr('ui.settings.tailnetSignIn.linkedTo', { login: data.linked })}{data.linked_at ? ` · ${tr('ui.settings.tailnetSignIn.linkedAgo', { ago: timeAgo(data.linked_at) })}` : ''}</span>
              <br />
              <span>{data.enabled ? tr('ui.settings.tailnetSignIn.linkedDetail', { login: data.linked }) : tr('ui.settings.tailnetSignIn.turnedOffOnThisServer')}</span>
            </>
          ) : seen ? tr('ui.settings.tailnetSignIn.youAreHereAs', { login: seen }) : tr('ui.settings.tailnetSignIn.notLinkedDetail')}
        >
          {data.linked && !confirming && (
            <button type="button" disabled={busy} onClick={() => void run(unlinkTailnet, tr('ui.settings.tailnetSignIn.couldNotUnlink'))}
              className={cn(buttonClass, 'border-transparent text-danger-ink')}>{tr('ui.settings.tailnetSignIn.unlink')}</button>
          )}
          {canLink && !confirming && (
            <button type="button" disabled={busy} onClick={() => setConfirming(true)} className={data.linked ? buttonClass : primaryButtonClass}>
              {data.linked ? tr('ui.settings.tailnetSignIn.linkLoginInstead', { login: seen }) : tr('ui.settings.tailnetSignIn.link')}
            </button>
          )}
        </SettingRow>
        {confirming && canLink && (
          <div className="px-3.5 py-3 text-[12.5px] leading-snug">
            <p>{tr('ui.settings.tailnetSignIn.confirm', { login: seen })}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button type="button" disabled={busy} onClick={() => void run(linkTailnet, tr('ui.settings.tailnetSignIn.couldNotLink'))} className={primaryButtonClass}>
                {busy ? tr('ui.settings.tailnetSignIn.linking') : tr('ui.settings.tailnetSignIn.linkLogin', { login: seen })}
              </button>
              <button type="button" disabled={busy} onClick={() => setConfirming(false)} className={buttonClass}>{tr('ui.settings.tailnetSignIn.cancel')}</button>
            </div>
          </div>
        )}
        {data.enabled && !seen && (
          <p className="px-3.5 py-3 text-[12px] leading-snug text-fg-dim">
            {tr('ui.settings.tailnetSignIn.openAtTailscaleAddress')}
            {privateAddress && (<>{' '}<a href={privateAddress} className="text-accent-ink hover:underline">{tr('ui.settings.tailnetSignIn.openItThere')}</a></>)}
          </p>
        )}
      </Group>
    </>
  );
}
