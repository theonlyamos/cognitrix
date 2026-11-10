import { useEffect, useRef, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import {
  CHATGPT_USAGE_URL, firstPlanSignIn, getChatGPTPlan, pastePlanAddress, signOutChatGPTPlan,
  startChatGPTSignIn, testChatGPTPlan, waitForChatGPTSignIn, type ChatGPTPlanStatus,
} from '@/lib/chatgptPlan';
import { Group, SettingRow, StatusPill, buttonClass, quietButtonClass } from './ui';

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
      setError(status.error || 'Sign-in did not finish.');
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
        setError(errorMessage(e, 'Could not start the ChatGPT sign-in.'));
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
      setError(errorMessage(e, 'That address did not work.'));
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
      <p role="status">Finish signing in in the browser tab that opened. This page updates by itself.</p>
      {/* A div, not a form: this also sits inside the first-run setup form. */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="w-full" htmlFor="chatgpt-address">
          If the page after sign-in says it can't be reached (Cognitrix runs on another computer), copy that page's
          address and paste it here:
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
          {signIn.pasting ? 'Checking…' : 'Use address'}
        </button>
        <button type="button" onClick={signIn.cancel} className={quietButtonClass}>Cancel</button>
      </div>
    </div>
  );
}

function Welcome({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-[rgba(5,6,7,.6)] p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="chatgpt-welcome" className="w-[420px] max-w-full rounded-2xl border border-line bg-panel p-6 shadow-panel">
        <h2 id="chatgpt-welcome" className="text-[18px] font-semibold">You're using your ChatGPT plan</h2>
        <p className="mt-2 text-[13.5px] leading-relaxed text-fg-dim">
          Agents set to the ChatGPT plan provider now run on your ChatGPT plan instead of an API key. You can see and
          limit what Cognitrix uses in{' '}
          <a href={CHATGPT_USAGE_URL} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">ChatGPT settings</a>.
        </p>
        <div className="mt-5 flex justify-end">
          <button type="button" autoFocus onClick={onClose} className={continueClass}>Got it</button>
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
    getChatGPTPlan().then(setStatus).catch((e) => setLoadError(errorMessage(e, 'Could not load the ChatGPT plan status.')));

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
      setResult({ ok: false, message: errorMessage(e, 'Could not run the test.') });
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
      setResult({ ok: false, message: errorMessage(e, 'Could not sign out.') });
    } finally {
      setBusy(null);
    }
  };

  const who = status.email || status.name;
  const detail = (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      {status.signed_in ? <StatusPill tone="ok">SIGNED IN</StatusPill>
        : status.needs_sign_in ? <StatusPill tone="warn">SIGN IN AGAIN</StatusPill>
        : <StatusPill tone="off">NOT CONNECTED</StatusPill>}
      {status.signed_in && who && <span>{who}{status.yours ? '' : ' (connected by someone else)'}</span>}
      {!status.signed_in && <span>Plus or Pro. Usage comes out of your plan, not an API bill.</span>}
    </span>
  );

  return (
    <>
      <Group className="mb-5">
        <SettingRow
          label="Use your ChatGPT plan"
          detail={detail}
          lead={<span aria-hidden="true" className="grid h-7 w-7 flex-none place-items-center rounded-[7px] bg-[#0d0d0d] font-mono text-[11px] font-medium text-white dark:bg-white dark:text-[#0d0d0d]">GP</span>}
        >
          {status.signed_in && (
            <>
              <button type="button" onClick={test} disabled={!!busy} className={quietButtonClass}>
                {busy === 'test' ? 'Testing…' : 'Test'}
              </button>
              <a href={CHATGPT_USAGE_URL} target="_blank" rel="noreferrer" className={quietButtonClass}>Manage usage</a>
              {status.can_edit && (
                <button type="button" onClick={signOut} disabled={!!busy} className={quietButtonClass}>
                  {busy === 'out' ? 'Signing out…' : 'Sign out'}
                </button>
              )}
            </>
          )}
          {!status.signed_in && status.can_edit && !signIn.flowId && (
            <>
              {status.previous_email && (
                <button type="button" onClick={() => void signIn.start(true)} className={quietButtonClass}>Use another account</button>
              )}
              <button type="button" onClick={() => void signIn.start(false)} className={continueClass}>Continue with ChatGPT</button>
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
          Pick <span className="font-medium text-fg">ChatGPT plan</span> as an agent's provider to run it on this account.
          {' '}Only the person who connected it can use it, and you can cap what Cognitrix uses in ChatGPT settings.
          {!status.can_edit && ' Only the admin can connect a plan to this engine.'}
          {' '}Claude and Gemini plans can't be used this way, because their terms only allow their own apps. Your Claude,
          ChatGPT or Copilot plan can still do coding work through the Coding Agent tool, which runs Claude Code, Codex or
          Copilot CLI signed in to your own account.
        </p>
      </Group>
      {welcome && <Welcome onClose={() => setWelcome(false)} />}
    </>
  );
}
