import { useEffect, useRef, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import {
  CHATGPT_USAGE_URL, firstPlanSignIn, getChatGPTPlan, pastePlanAddress, signOutChatGPTPlan,
  startChatGPTSignIn, testChatGPTPlan, waitForChatGPTSignIn, type ChatGPTPlanStatus,
} from '@/lib/chatgptPlan';
import { Group, SettingRow, StatusPill, buttonClass, quietButtonClass } from './ui';
import { tr } from '@/lib/i18n';

type Result = { ok: boolean; message: string };

const continueClass =
  'inline-flex h-9 items-center rounded-lg bg-[#0d0d0d] px-3.5 text-[12.5px] font-medium text-white hover:bg-[#2a2a2a] disabled:opacity-60 dark:bg-white dark:text-[#0d0d0d] dark:hover:bg-[#e6e6e6] md:h-8';

/** "Continue with ChatGPT", the wait for the browser, and the paste fallback. */
export function useChatGPTSignIn(onDone: () => void) {
  const [flowId, setFlowId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [address, setAddress] = useState('');
  const [pasting, setPasting] = useState(false);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  const finish = (status: { status: string; error: string | null }) => {
    setFlowId(null);
    setAddress('');
    abort.current?.abort();
    if (status.status === 'done') {
      setError(null);
      onDone();
    } else {
      setError(status.error || tr('ui.settings.chatGPTPlan.signInDidNotFinish'));
    }
  };

  const start = async (newAccount = false) => {
    setError(null);
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    try {
      const flow = await startChatGPTSignIn(newAccount);
      setFlowId(flow.flow_id);
      const result = await waitForChatGPTSignIn(flow.flow_id, { signal: controller.signal });
      if (!controller.signal.aborted) finish(result);
    } catch (e) {
      if (!controller.signal.aborted) {
        setFlowId(null);
        setError(errorMessage(e, tr('ui.settings.chatGPTPlan.couldNotStartTheChatgpt')));
      }
    }
  };

  const paste = async () => {
    if (!flowId || !address.trim()) return;
    setPasting(true);
    try {
      const result = await pastePlanAddress(flowId, address.trim());
      if (result.status !== 'pending') finish(result);
    } catch (e) {
      setError(errorMessage(e, tr('ui.settings.chatGPTPlan.thatAddressDidNotWork')));
    } finally {
      setPasting(false);
    }
  };

  const cancel = () => {
    abort.current?.abort();
    setFlowId(null);
    setAddress('');
  };

  return { flowId, error, address, setAddress, pasting, start, paste, cancel };
}

/** Shown while the browser sign-in is open. */
export function SignInWaiting({ signIn }: { signIn: ReturnType<typeof useChatGPTSignIn> }) {
  if (!signIn.flowId) return null;
  return (
    <div className="space-y-2 px-3.5 pb-3 text-[12px] leading-relaxed text-fg-dim">
      <p role="status">{tr('ui.settings.chatGPTPlan.finishSigningInInThe')}</p>
      {/* A div, not a form: this also sits inside the first-run setup form. */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="w-full" htmlFor="chatgpt-address">
          {tr('ui.settings.chatGPTPlan.ifThePageAfterSign')}
        </label>
        <Input
          id="chatgpt-address"
          autoComplete="off"
          placeholder="http://127.0.0.1:1455/auth/callback?code=…"
          value={signIn.address}
          onChange={(e) => signIn.setAddress(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void signIn.paste(); } }}
          className="min-w-0 flex-1 basis-[240px] font-mono text-[12px] md:h-9"
        />
        <button type="button" onClick={() => void signIn.paste()} disabled={signIn.pasting || !signIn.address.trim()} className={buttonClass}>
          {signIn.pasting ? tr('ui.settings.chatGPTPlan.checking') : tr('ui.settings.chatGPTPlan.useAddress')}
        </button>
        <button type="button" onClick={signIn.cancel} className={quietButtonClass}>{tr('ui.settings.chatGPTPlan.cancel')}</button>
      </div>
    </div>
  );
}

function Welcome({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-[rgba(5,6,7,.6)] p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="chatgpt-welcome" className="w-[420px] max-w-full rounded-2xl border border-line bg-panel p-6 shadow-panel">
        <h2 id="chatgpt-welcome" className="text-[18px] font-semibold">{tr('ui.settings.chatGPTPlan.youReUsingYourChatgpt')}</h2>
        <p className="mt-2 text-[13.5px] leading-relaxed text-fg-dim">
          {tr('ui.settings.chatGPTPlan.agentsSetToTheChatgpt')}{' '}
          <a href={CHATGPT_USAGE_URL} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{tr('ui.settings.chatGPTPlan.chatgptSettings')}</a>.
        </p>
        <div className="mt-5 flex justify-end">
          <button type="button" autoFocus onClick={onClose} className={continueClass}>{tr('ui.settings.chatGPTPlan.gotIt')}</button>
        </div>
      </div>
    </div>
  );
}

/** Settings › Models and keys: connect a ChatGPT Plus or Pro plan. */
export function ChatGPTPlanCard() {
  const [status, setStatus] = useState<ChatGPTPlanStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'test' | 'out' | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [welcome, setWelcome] = useState(false);

  const load = () =>
    getChatGPTPlan().then(setStatus).catch((e) => setLoadError(errorMessage(e, tr('ui.settings.chatGPTPlan.couldNotLoadTheChatgpt'))));

  const signIn = useChatGPTSignIn(() => {
    void load();
    if (firstPlanSignIn()) setWelcome(true);
  });

  useEffect(() => { void load(); }, []);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!status) return null;

  const test = async () => {
    setBusy('test');
    setResult(null);
    try {
      setResult(await testChatGPTPlan());
    } catch (e) {
      setResult({ ok: false, message: errorMessage(e, tr('ui.settings.chatGPTPlan.couldNotRunTheTest')) });
    } finally {
      setBusy(null);
    }
  };

  const signOut = async () => {
    setBusy('out');
    setResult(null);
    try {
      const res = await signOutChatGPTPlan();
      if (res.warning) setResult({ ok: false, message: res.warning });
      await load();
    } catch (e) {
      setResult({ ok: false, message: errorMessage(e, tr('ui.settings.chatGPTPlan.couldNotSignOut')) });
    } finally {
      setBusy(null);
    }
  };

  const who = status.email || status.name;
  const detail = (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      {status.signed_in ? <StatusPill tone="ok">{tr('ui.settings.chatGPTPlan.signedIn')}</StatusPill>
        : status.needs_sign_in ? <StatusPill tone="warn">{tr('ui.settings.chatGPTPlan.signInAgain')}</StatusPill>
        : <StatusPill tone="off">{tr('ui.settings.chatGPTPlan.notConnected')}</StatusPill>}
      {status.signed_in && who && <span>{who}{status.yours ? '' : tr('ui.settings.chatGPTPlan.connectedBySomeoneElse')}</span>}
      {!status.signed_in && <span>{tr('ui.settings.chatGPTPlan.plusOrProUsageComes')}</span>}
    </span>
  );

  return (
    <>
      <Group className="mb-5">
        <SettingRow
          label={tr('ui.settings.chatGPTPlan.useYourChatgptPlan')}
          detail={detail}
          lead={<span aria-hidden="true" className="grid h-7 w-7 flex-none place-items-center rounded-[7px] bg-[#0d0d0d] font-mono text-[11px] font-medium text-white dark:bg-white dark:text-[#0d0d0d]">GP</span>}
        >
          {status.signed_in && (
            <>
              <button type="button" onClick={test} disabled={!!busy} className={quietButtonClass}>
                {busy === 'test' ? tr('ui.settings.chatGPTPlan.testing') : tr('ui.settings.chatGPTPlan.test')}
              </button>
              <a href={CHATGPT_USAGE_URL} target="_blank" rel="noreferrer" className={quietButtonClass}>{tr('ui.settings.chatGPTPlan.manageUsage')}</a>
              {status.can_edit && (
                <button type="button" onClick={signOut} disabled={!!busy} className={quietButtonClass}>
                  {busy === 'out' ? tr('ui.settings.chatGPTPlan.signingOut') : tr('ui.settings.chatGPTPlan.signOut')}
                </button>
              )}
            </>
          )}
          {!status.signed_in && status.can_edit && !signIn.flowId && (
            <>
              {status.previous_email && (
                <button type="button" onClick={() => void signIn.start(true)} className={quietButtonClass}>{tr('ui.settings.chatGPTPlan.useAnotherAccount')}</button>
              )}
              <button type="button" onClick={() => void signIn.start(false)} className={continueClass}>{tr('ui.settings.chatGPTPlan.continueWithChatgpt')}</button>
            </>
          )}
        </SettingRow>
        <SignInWaiting signIn={signIn} />
        {(signIn.error || result) && (
          <p role={result?.ok ? 'status' : 'alert'} className={`px-3.5 pb-3 text-[12px] ${result?.ok ? 'text-ok' : 'text-danger-ink'}`}>
            {signIn.error || result?.message}
          </p>
        )}
        <p className="px-3.5 pb-3 text-[12px] leading-relaxed text-fg-dim">
          {tr('ui.settings.chatGPTPlan.pick')}{' '}<span className="font-medium text-fg">{tr('ui.settings.chatGPTPlan.chatgptPlan')}</span>{' '}{tr('ui.settings.chatGPTPlan.asAnAgentSProvider', { value: !status.can_edit && tr('ui.settings.chatGPTPlan.onlyTheAdminCanConnect') })}
        </p>
      </Group>
      {welcome && <Welcome onClose={() => setWelcome(false)} />}
    </>
  );
}
