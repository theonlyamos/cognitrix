import { useCallback, useEffect, useState } from 'react';
import { KeyRound } from 'lucide-react';
import { errorMessage } from '@/lib/api';
import { desktop, isDesktop } from '@/lib/desktop';
import {
  addPasskey, browserStepStatus, browserStepUrl, canUsePasskeysHere, cancelBrowserStep, listPasskeys, localhostAddress,
  removePasskey, renamePasskey, startBrowserStep, wasCanceled, type BrowserStep, type Passkey, type PasskeyList,
} from '@/lib/passkeys';
import { timeAgo } from '@/lib/agentDraft';
import { Group, SettingRow, StatusPill, SubHeading, buttonClass, primaryButtonClass } from '@/components/settings/ui';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

function PasskeyRow({ passkey, onChanged, onError }: { passkey: Passkey; onChanged: () => void; onError: (m: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(passkey.name);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const otherSite = typeof window !== 'undefined' && passkey.site && passkey.site !== window.location.hostname
    && !(isDesktop() && passkey.site === 'localhost');

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await renamePasskey(passkey.id, name);
      setEditing(false);
      onChanged();
    } catch (err) {
      onError(errorMessage(err, tr('ui.settings.passkeys.couldNotRenameThePasskey')));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await removePasskey(passkey.id);
      onChanged();
    } catch (err) {
      onError(errorMessage(err, tr('ui.settings.passkeys.couldNotRemoveThePasskey')));
      setBusy(false);
    }
  };

  const used = passkey.last_used_at ? tr('ui.settings.passkeys.lastUsedAgo', { ago: timeAgo(passkey.last_used_at) }) : tr('ui.settings.passkeys.notUsedYet');
  return (
    <SettingRow
      lead={<span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-panel-2 text-fg-dim"><KeyRound size={17} aria-hidden /></span>}
      label={editing ? (
        <form onSubmit={save} className="flex items-center gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={80}
            autoFocus
            aria-label={tr('ui.settings.passkeys.passkeyName')}
            onKeyDown={(e) => { if (e.key === 'Escape') { setEditing(false); setName(passkey.name); } }}
            className="h-8 min-w-0 flex-1 rounded-md border border-line bg-panel px-2 text-[13px] outline-none focus:border-accent"
          />
          <button type="submit" disabled={busy || !name.trim()} className={buttonClass}>{tr('ui.settings.passkeys.save')}</button>
        </form>
      ) : (
        <span className="inline-flex flex-wrap items-center gap-2">
          {passkey.name}
          {passkey.synced && <StatusPill tone="ok">{tr('ui.settings.passkeys.synced')}</StatusPill>}
        </span>
      )}
      detail={(
        <>
          <span>{[passkey.provider && passkey.provider !== passkey.name ? passkey.provider : '', tr('ui.settings.passkeys.addedAgo', { ago: timeAgo(passkey.created_at) }), used].filter(Boolean).join(' · ')}</span>
          {otherSite && (<><br /><span>{tr('ui.settings.passkeys.worksOnSite', { site: passkey.site })}</span></>)}
        </>
      )}
    >
      {!editing && (
        <div className="flex flex-wrap justify-end gap-1">
          {!confirming && (
            <button type="button" onClick={() => setEditing(true)} className={cn(buttonClass, 'border-transparent')}>{tr('ui.settings.passkeys.rename')}</button>
          )}
          {confirming ? (
            <>
              <button type="button" onClick={() => setConfirming(false)} className={cn(buttonClass, 'border-transparent')}>{tr('ui.settings.passkeys.keep')}</button>
              <button type="button" onClick={() => void remove()} disabled={busy} className={cn(buttonClass, 'text-danger-ink')}>
                {busy ? tr('ui.settings.passkeys.removing') : tr('ui.settings.passkeys.removeIt')}
              </button>
            </>
          ) : (
            <button type="button" onClick={() => setConfirming(true)} className={cn(buttonClass, 'border-transparent text-danger-ink')}>{tr('ui.settings.passkeys.remove')}</button>
          )}
        </div>
      )}
    </SettingRow>
  );
}

/** Settings › Sign-ins › Passkeys: add, rename and remove passkeys. */
export function PasskeysSettings() {
  const [data, setData] = useState<PasskeyList | null>(null);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [step, setStep] = useState<BrowserStep | null>(null);
  const [added, setAdded] = useState('');
  const here = canUsePasskeysHere();
  const atLocalhost = !isDesktop() && !here ? localhostAddress() : '';

  const load = useCallback(() => {
    listPasskeys().then((d) => { setData(d); })
      .catch((err) => setError(errorMessage(err, tr('ui.settings.passkeys.couldNotLoadYourPasskeys'))));
  }, []);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    setError('');
    setAdded('');
    setAdding(true);
    try {
      if (here) {
        const passkey = await addPasskey();
        setAdded(passkey.name);
        load();
      } else {
        setStep(await startBrowserStep('register'));
      }
    } catch (err) {
      if (!wasCanceled(err)) setError(errorMessage(err, tr('ui.settings.passkeys.couldNotAddThePasskey')));
    } finally {
      setAdding(false);
    }
  };

  // Desktop app: the browser makes the passkey; wait for it.
  useEffect(() => {
    if (!step) return;
    let alive = true;
    const timer = setInterval(() => {
      browserStepStatus(step.secret).then((s) => {
        if (!alive) return;
        if (s.status === 'done') {
          setStep(null);
          if (s.kind === 'register' && s.passkey) setAdded(s.passkey.name);
          load();
        } else if (s.status === 'expired') {
          setStep(null);
          setError(tr('ui.settings.passkeys.thePasskeyStepExpiredTry'));
        }
      }).catch(() => {});
    }, 1500);
    return () => { alive = false; clearInterval(timer); };
  }, [step, load]);

  const cancelStep = () => {
    if (step) void cancelBrowserStep(step.secret).catch(() => {});
    setStep(null);
  };

  const canAdd = !!data?.enabled && (here || (isDesktop() && data.handoff));
  const list = data?.passkeys ?? [];

  return (
    <>
      <div className="mb-2 mt-7 flex items-center justify-between gap-3">
        <SubHeading className="mb-0 mt-0">{tr('ui.settings.passkeys.passkeys')}{data ? ` · ${list.length}` : ''}</SubHeading>
        {canAdd && (
          <button type="button" onClick={() => void add()} disabled={adding || !!step} className={primaryButtonClass}>
            {adding ? tr('ui.settings.passkeys.waitingForYourPasskey') : tr('ui.settings.passkeys.addAPasskey')}
          </button>
        )}
      </div>
      {step && (
        <div role="status" className="mb-2 rounded-lg border border-line bg-panel-2 px-3.5 py-3 text-[12.5px] leading-snug">
          <span className="font-medium">{tr('ui.settings.passkeys.finishInYourBrowser')}</span>{' '}
          <span className="text-fg-dim">{tr('ui.settings.passkeys.thisWindowCanTShow')}</span>
          <div className="mt-1.5 flex gap-3">
            <button type="button" className="text-accent-ink hover:underline" onClick={() => void desktop.openExternal(browserStepUrl(step))}>{tr('ui.settings.passkeys.openItAgain')}</button>
            <button type="button" className="text-fg-dim hover:underline" onClick={cancelStep}>{tr('ui.settings.passkeys.cancel')}</button>
          </div>
        </div>
      )}
      {added && <p role="status" className="mb-2 text-[12.5px] text-ok">{tr('ui.settings.passkeys.addedAddedYouCanSign', { added })}</p>}
      {error && <p role="alert" className="mb-2 text-[12.5px] text-danger-ink">{error}</p>}
      {!data ? (
        !error && <p role="status" className="text-[13px] text-fg-dim">{tr('ui.settings.passkeys.loading')}</p>
      ) : (
        <Group>
          {list.map((p) => <PasskeyRow key={p.id} passkey={p} onChanged={load} onError={setError} />)}
          {list.length === 0 && (
            <p className="px-3.5 py-3 text-[12.5px] leading-snug text-fg-dim">
              {!data.enabled
                ? tr('ui.settings.passkeys.passkeysAreTurnedOffOn')
                : tr('ui.settings.passkeys.noPasskeysYetAddOne')}
            </p>
          )}
          {data.enabled && !canAdd && (
            <p className="px-3.5 py-3 text-[12px] leading-snug text-fg-dim">
              {atLocalhost ? (
                <>
                  {tr('ui.settings.passkeys.passkeysDonTWorkOn')}{' '}
                  <a href={atLocalhost} className="text-accent-ink hover:underline">{tr('ui.settings.passkeys.openCognitrixAtLocalhost')}</a>
                </>
              ) : tr('ui.settings.passkeys.thisBrowserCanTMake')}
            </p>
          )}
        </Group>
      )}
    </>
  );
}
