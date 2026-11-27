import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { KeyRound } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { useUser } from '@/context/AppContext';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { ThemeToggle } from '@/components/ThemeToggle';
import OAuthButtons from '@/components/OAuthButtons';
import { useT, tr } from '@/lib/i18n';
import { takeAfterSignIn } from '@/lib/afterSignIn';
import { codeFromAddress } from '@/lib/signIns';
import { desktop, isDesktop } from '@/lib/desktop';
import {
  autofillAvailable, browserStepStatus, browserStepUrl, canUsePasskeysHere, cancelBrowserStep, localhostAddress,
  signInWithPasskey, startBrowserStep, wasCanceled, type BrowserStep, type SignedIn,
} from '@/lib/passkeys';
import { cn } from '@/lib/utils';

/** 'KXRT4M9P' -> 'KXRT-4M9P' while typing; drops what can't be in a code. */
function formatCode(value: string): string {
  const raw = value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
  return raw.length > 4 ? `${raw.slice(0, 4)}-${raw.slice(4)}` : raw;
}

const GRID = {
  backgroundImage:
    'linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px)',
  backgroundSize: '32px 32px',
};

export default function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useUser();
  const navigate = useNavigate();
  const t = useT();
  const [options, setOptions] = useState({
    signup_open: true, password_reset_by_email: false, code_sign_in: true, passkeys: false, passkey_handoff: false,
  });
  const [initialCode] = useState(codeFromAddress);
  const [mode, setMode] = useState<'password' | 'code'>(initialCode ? 'code' : 'password');
  const [code, setCode] = useState(initialCode);

  // A scanned QR code puts the code in the address: keep it out of history once read.
  useEffect(() => {
    if (initialCode) window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }, [initialCode]);
  const [forgotEmail, setForgotEmail] = useState('');
  const [forgotOpen, setForgotOpen] = useState(false);
  const [forgotState, setForgotState] = useState<'idle' | 'sending' | 'sent' | 'failed'>('idle');

  useEffect(() => {
    api.get('/auth/options').then(({ data }) => {
      // A new server: say where its database and job queue are first.
      if (data.server_setup) navigate('/setup', { replace: true });
      else setOptions((o) => ({ ...o, ...data }));
    }).catch(() => {});
  }, [navigate]);

  const sendReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setForgotState('sending');
    try {
      await api.post('/auth/forgot', { email: forgotEmail || email });
      setForgotState('sent');
    } catch {
      setForgotState('failed');
    }
  };

  // ---- Passkeys -------------------------------------------------------------
  const passkeyHere = canUsePasskeysHere();
  const showPasskey = options.passkeys && (passkeyHere || options.passkey_handoff);
  const [passkeyBusy, setPasskeyBusy] = useState(false);
  const [browserStep, setBrowserStep] = useState<BrowserStep | null>(null);
  const autofill = useRef<AbortController | null>(null);

  const signedIn = (data: SignedIn) => {
    login(data.user as unknown as Parameters<typeof login>[0], data.access_token);
    navigate(takeAfterSignIn());
  };

  // The browser offers saved passkeys in the email field's autofill.
  useEffect(() => {
    if (!options.passkeys || !passkeyHere || mode !== 'password') return;
    const ctrl = new AbortController();
    autofill.current = ctrl;
    autofillAvailable().then((ok) => {
      if (!ok || ctrl.signal.aborted) return;
      signInWithPasskey({ conditional: true, signal: ctrl.signal }).then(signedIn).catch((err) => {
        // Only the server's answer is worth showing here: the browser's own autofill
        // errors (no passkey, prompt dismissed) are not something the person asked for.
        if (!ctrl.signal.aborted && axios.isAxiosError(err)) setError(errorMessage(err, tr('ui.login.thatPasskeyDidNotWork')));
      });
    });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.passkeys, passkeyHere, mode]);

  const handlePasskey = async () => {
    setError('');
    autofill.current?.abort();
    if (!passkeyHere) {
      // The desktop app's window can't show the prompt: finish in the browser.
      setPasskeyBusy(true);
      try {
        setBrowserStep(await startBrowserStep('sign_in'));
      } catch (err) {
        setError(errorMessage(err, tr('ui.login.couldNotOpenTheBrowser')));
      } finally {
        setPasskeyBusy(false);
      }
      return;
    }
    setPasskeyBusy(true);
    try {
      signedIn(await signInWithPasskey());
    } catch (err) {
      if (!wasCanceled(err)) setError(errorMessage(err, tr('ui.login.thatPasskeyDidNotWork')));
    } finally {
      setPasskeyBusy(false);
    }
  };

  // Desktop app: wait for the browser to finish the passkey step.
  useEffect(() => {
    if (!browserStep) return;
    let alive = true;
    const timer = setInterval(() => {
      browserStepStatus(browserStep.secret).then((s) => {
        if (!alive) return;
        if (s.status === 'done' && s.kind === 'sign_in') {
          alive = false;
          setBrowserStep(null);
          signedIn(s);
        } else if (s.status === 'expired') {
          setBrowserStep(null);
          setError(tr('ui.login.thePasskeyStepExpiredTry'));
        }
      }).catch(() => {});
    }, 1500);
    return () => { alive = false; clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [browserStep]);

  const cancelStep = () => {
    if (browserStep) void cancelBrowserStep(browserStep.secret).catch(() => {});
    setBrowserStep(null);
  };

  const passkeyButton = showPasskey ? (
    <button
      type="button"
      onClick={() => void handlePasskey()}
      disabled={passkeyBusy || !!browserStep}
      className="flex h-11 w-full items-center justify-center gap-2.5 rounded-md border border-line bg-panel text-[14px] font-medium transition-colors hover:bg-panel-2 disabled:opacity-60"
    >
      <KeyRound size={16} aria-hidden />
      {passkeyBusy ? tr('ui.login.waitingForYourPasskey') : tr('ui.login.signInWithAPasskey')}
    </button>
  ) : null;
  const atLocalhost = options.passkeys && !isDesktop() && !passkeyHere ? localhostAddress() : '';

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const { data } = await api.post('/auth/login', {
        username: email, password, ...(isDesktop() ? { device_name: 'Cognitrix desktop app', device_kind: 'desktop' } : {}),
      });
      login(data.user, data.access_token);
      navigate(takeAfterSignIn());
    } catch (err) {
      setError(errorMessage(err, t('login.wrong')));
    } finally {
      setLoading(false);
    }
  };

  const handleCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const { data } = await api.post('/auth/code-sign-in', {
        code, ...(isDesktop() ? { device_name: 'Cognitrix desktop app', device_kind: 'desktop' } : {}),
      });
      login(data.user, data.access_token);
      navigate(takeAfterSignIn());
    } catch (err) {
      setError(errorMessage(err, tr('ui.login.thatCodeDidNotWork')));
    } finally {
      setLoading(false);
    }
  };

  const tab = (value: 'password' | 'code', label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={mode === value}
      onClick={() => { setMode(value); setError(''); }}
      className={cn('h-9 flex-1 rounded-md text-[13px]', mode === value ? 'bg-panel font-medium text-fg shadow-sm' : 'text-fg-dim hover:text-fg')}
    >
      {label}
    </button>
  );

  return (
    <div className="min-h-screen flex bg-bg text-fg">
      {/* Brand panel */}
      <aside className="relative hidden lg:flex lg:w-[45%] flex-col justify-between overflow-hidden border-r border-line bg-panel p-12">
        <div className="absolute inset-0 opacity-60" style={GRID} aria-hidden />
        <div className="relative flex items-center gap-3">
          <span className="grid h-7 w-7 place-items-center rounded-sm bg-accent text-accent-foreground">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
          </span>
          <span className="font-mono text-[13px] font-bold tracking-[0.14em]">
            {tr('ui.login.cognitrix')}<span className="text-fg-dim font-medium"> /v{__APP_VERSION__}</span>
          </span>
        </div>

        <div className="relative max-w-md">
          <h2 className="text-4xl font-bold leading-[1.05] tracking-tight">
            {t('login.heroTitle1')}<br />{t('login.heroTitle2')}
          </h2>
          <p className="mt-4 text-fg-dim max-w-sm">
            {t('login.heroBody')}
          </p>
        </div>

        <span aria-hidden />
      </aside>

      {/* Form column */}
      <main className="flex flex-1 flex-col">
        <header className="flex min-h-14 flex-nowrap items-center justify-between p-6">
          <span className="font-mono text-[11px] tracking-[0.16em] text-fg-dim lg:hidden">{tr('ui.login.cognitrix')}</span>
          <span className="lg:hidden" />
          <ThemeToggle className="ml-auto" />
        </header>

        <div className="flex flex-1 items-center px-6 pb-16">
          <div className="w-full max-w-sm mx-auto lg:mx-0 lg:ml-16 animate-rise">
            <p className="font-mono text-[11px] tracking-[0.18em] text-accent-ink">{t('login.kicker')}</p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight">{t('login.title')}</h1>
            <p className="mt-2 text-fg-dim">{t('login.subtitle')}</p>

            <OAuthButtons mode="signin" first={passkeyButton} />
            {browserStep && (
              <div role="status" className="mt-3 rounded-lg border border-line bg-panel-2 px-3.5 py-3 text-[13px] leading-snug">
                <p className="font-medium">{tr('ui.login.finishInYourBrowser')}</p>
                <p className="mt-1 text-fg-dim">{tr('ui.login.weOpenedYourBrowserUse')}</p>
                <div className="mt-2 flex flex-wrap gap-3">
                  <button type="button" className="text-accent-ink hover:underline"
                    onClick={() => void desktop.openExternal(browserStepUrl(browserStep))}>{tr('ui.login.openItAgain')}</button>
                  <button type="button" className="text-fg-dim hover:underline" onClick={cancelStep}>{tr('ui.login.cancel')}</button>
                </div>
              </div>
            )}
            {atLocalhost && (
              <p className="mt-6 text-[12.5px] leading-snug text-fg-dim">
                {tr('ui.login.passkeysDonTWorkOn')}{' '}
                <a href={atLocalhost} className="text-accent-ink hover:underline">{tr('ui.login.openThisPageAtLocalhost')}</a>
              </p>
            )}
            {options.code_sign_in && (
              <div role="tablist" aria-label={tr('ui.login.howToSignIn')} className="mt-6 flex gap-0.5 rounded-lg bg-panel-2 p-1">
                {tab('password', tr('ui.login.password'))}
                {tab('code', tr('ui.login.signInCode'))}
              </div>
            )}
            {mode === 'code' && options.code_sign_in ? (
              <form onSubmit={handleCode} className="mt-6 space-y-4">
                <div className="space-y-1.5">
                  <label htmlFor="signin-code" className="block font-mono text-[11px] tracking-[0.12em] text-fg-dim">{tr('ui.login.codeFromASignedInDevice')}</label>
                  <Input id="signin-code" value={code} onChange={(e) => setCode(formatCode(e.target.value))}
                    placeholder="XXXX-XXXX" required autoComplete="one-time-code" autoCapitalize="characters" spellCheck={false}
                    inputMode="text" autoFocus className="h-12 text-center font-mono text-[20px] tracking-[0.2em]" />
                </div>
                <p className="text-[13px] leading-relaxed text-fg-dim">{tr('ui.login.onADeviceWhereYou')}</p>
                {error && (
                  <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>
                )}
                <Button type="submit" disabled={loading || code.length !== 9} className="w-full">
                  {loading ? (
                    <span className="inline-flex items-center gap-2"><Spinner /> {t('login.submitting')}</span>
                  ) : (
                    <>{t('login.submit')} <span aria-hidden>→</span></>
                  )}
                </Button>
              </form>
            ) : (
            <form onSubmit={handleLogin} className="mt-6 space-y-5">
              <div className="space-y-1.5">
                <label htmlFor="email" className="block font-mono text-[11px] tracking-[0.12em] text-fg-dim">{t('auth.email')}</label>
                <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com" required autoComplete="username webauthn" autoFocus />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="password" className="block font-mono text-[11px] tracking-[0.12em] text-fg-dim">{t('auth.password')}</label>
                <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  required autoComplete="current-password" />
              </div>

              {error && (
                <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>
              )}

              <Button type="submit" disabled={loading} className="w-full">
                {loading ? (
                  <span className="inline-flex items-center gap-2"><Spinner /> {t('login.submitting')}</span>
                ) : (
                  <>{t('login.submit')} <span aria-hidden>→</span></>
                )}
              </Button>
              {loading && <span role="status" className="sr-only">{t('login.submitting')}</span>}
            </form>
            )}

            <div className="mt-5 text-sm text-fg-dim">
              <button type="button" aria-expanded={forgotOpen} onClick={() => setForgotOpen((v) => !v)}
                className="min-h-11 text-accent-ink underline-offset-2 hover:underline md:min-h-0">
                {t('login.forgot')}
              </button>
              {!forgotOpen ? null : options.password_reset_by_email ? (
                forgotState === 'sent' ? (
                  <p role="status" className="mt-2 leading-relaxed">
                    {t('login.forgot.sent')}
                  </p>
                ) : (
                  <form onSubmit={sendReset} className="mt-2 space-y-2" aria-label={tr('ui.login.resetPasswordByEmail')}>
                    <Input type="email" required aria-label={tr('ui.login.yourAccountEmail')} placeholder="you@example.com"
                      value={forgotEmail || email} onChange={(e) => setForgotEmail(e.target.value)} autoComplete="email" />
                    <Button type="submit" variant="outline" disabled={forgotState === 'sending'} className="w-full">
                      {forgotState === 'sending' ? t('login.forgot.sending') : t('login.forgot.send')}
                    </Button>
                    {forgotState === 'failed' && (
                      <p role="alert" className="font-mono text-[12px] text-danger-ink">{t('login.forgot.failed')}</p>
                    )}
                  </form>
                )
              ) : (
                <p className="mt-2 leading-relaxed">
                  {t('login.forgot.noEmail')}{' '}
                  <code className="rounded bg-panel-2 px-1 py-0.5 font-mono text-[12px] text-fg">cognitrix reset-password you@example.com</code>.
                </p>
              )}
            </div>

            {options.signup_open ? (
              <p className="mt-6 text-sm text-fg-dim">
                {t('login.noAccount')}{' '}
                <Link to="/signup" className="text-accent-ink font-medium hover:underline underline-offset-4">{t('login.create')}</Link>
              </p>
            ) : (
              <p className="mt-6 text-sm text-fg-dim">{t('login.inviteOnly')}</p>
            )}
          </div>
        </div>

        <footer className="px-6 pb-6 font-mono text-[10.5px] text-fg-dim lg:ml-16">
          {t('login.terms')}
        </footer>
      </main>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" className="opacity-25" />
      <path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  );
}
