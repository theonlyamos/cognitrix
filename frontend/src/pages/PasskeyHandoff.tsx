import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { KeyRound } from 'lucide-react';
import { errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { ThemeToggle } from '@/components/ThemeToggle';
import { canUsePasskeysHere, finishHandoff, handoffInfo, wasCanceled, type HandoffInfo } from '@/lib/passkeys';
import { tr } from '@/lib/i18n';

/**
 * The desktop app's passkey step, in the default browser (the app's own
 * window can't use passkeys). The app opened this page at
 * http://localhost:<port>/passkey?handoff=…, and picks up the result itself.
 */
export default function PasskeyHandoff() {
  const handoff = useSearchParams()[0].get('handoff') || '';
  const [info, setInfo] = useState<HandoffInfo | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<'sign_in' | 'register' | ''>('');
  const [account, setAccount] = useState('');
  const supported = canUsePasskeysHere();

  useEffect(() => {
    if (!handoff) {
      setError(tr('ui.passkeyHandoff.thisLinkIsMissingIts'));
      return;
    }
    handoffInfo(handoff).then(setInfo)
      .catch((err) => setError(errorMessage(err, tr('ui.passkeyHandoff.thisLinkHasExpiredStart'))));
  }, [handoff]);

  const go = async () => {
    setError('');
    setBusy(true);
    try {
      const out = await finishHandoff(handoff);
      setAccount(out.account || info?.account || '');
      setDone(out.kind);
    } catch (err) {
      if (!wasCanceled(err)) setError(errorMessage(err, tr('ui.passkeyHandoff.thePasskeyStepDidNot')));
    } finally {
      setBusy(false);
    }
  };

  const adding = info?.kind === 'register';

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      <header className="flex min-h-14 items-center justify-between p-6">
        <span className="font-mono text-[11px] tracking-[0.16em] text-fg-dim">{tr('ui.passkeyHandoff.cognitrix')}</span>
        <ThemeToggle className="ml-auto" />
      </header>
      <main className="flex flex-1 items-center px-6 pb-16">
        <div className="mx-auto w-full max-w-sm animate-rise">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-panel-2 text-accent-ink">
            <KeyRound size={20} aria-hidden />
          </span>
          {done ? (
            <>
              <h1 className="mt-4 text-2xl font-bold tracking-tight">
                {done === 'register' ? tr('ui.passkeyHandoff.passkeyAdded') : tr('ui.passkeyHandoff.youReSignedIn')}
              </h1>
              <p role="status" className="mt-2 leading-relaxed text-fg-dim">
                {done === 'register'
                  ? tr('ui.passkeyHandoff.yourNewPasskeyIsSaved')
                  : account
                    ? tr('ui.passkeyHandoff.theCognitrixAppIsNow', { account })
                    : tr('ui.passkeyHandoff.theCognitrixAppIsNow_f32a')}
              </p>
            </>
          ) : (
            <>
              <h1 className="mt-4 text-2xl font-bold tracking-tight">
                {adding ? tr('ui.passkeyHandoff.addAPasskey') : tr('ui.passkeyHandoff.signInToTheCognitrix')}
              </h1>
              <p className="mt-2 leading-relaxed text-fg-dim">
                {adding
                  ? tr('ui.passkeyHandoff.theCognitrixAppOnThis', { account: info?.account || 'your account' })
                  : tr('ui.passkeyHandoff.theCognitrixAppOnThis_5b59')}
              </p>
              {!supported && (
                <p role="alert" className="mt-4 border-l-2 border-warn-ink bg-panel-2 px-3 py-2 text-[13px] text-warn-ink">
                  {tr('ui.passkeyHandoff.thisBrowserCanTUse')}
                </p>
              )}
              {error && (
                <p role="alert" className="mt-4 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>
              )}
              <Button type="button" onClick={go} disabled={!info || busy || !supported} className="mt-6 w-full">
                {busy ? tr('ui.passkeyHandoff.waitingForYourPasskey') : adding ? tr('ui.passkeyHandoff.makeAPasskey') : tr('ui.passkeyHandoff.useMyPasskey')}
              </Button>
              <p className="mt-4 text-[12.5px] leading-snug text-fg-dim">
                {tr('ui.passkeyHandoff.didnTStartThisIn')}
              </p>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
