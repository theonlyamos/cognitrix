import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useUser } from '@/context/AppContext';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { ThemeToggle } from '@/components/ThemeToggle';
import OAuthButtons from '@/components/OAuthButtons';
import { useT } from '@/lib/i18n';

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
  const [options, setOptions] = useState({ signup_open: true, password_reset_by_email: false });
  const [forgotEmail, setForgotEmail] = useState('');
  const [forgotState, setForgotState] = useState<'idle' | 'sending' | 'sent' | 'failed'>('idle');

  useEffect(() => {
    api.get('/auth/options').then(({ data }) => setOptions((o) => ({ ...o, ...data }))).catch(() => {});
  }, []);

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

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const { data } = await api.post('/auth/login', { username: email, password });
      login(data.user, data.access_token);
      navigate('/home');
    } catch (err) {
      setError(errorMessage(err, t('login.wrong')));
    } finally {
      setLoading(false);
    }
  };

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
            COGNITRIX<span className="text-fg-dim font-medium"> /v{__APP_VERSION__}</span>
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

        <div className="relative font-mono text-[11px] text-fg-dim space-y-1.5">
          <div className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-accent" /> providers · openrouter · openai · google · groq · ollama</div>
          <div className="text-fg-dim/70">status &nbsp;ready · api/v1 · ws + sse</div>
        </div>
      </aside>

      {/* Form column */}
      <main className="flex flex-1 flex-col">
        <header className="flex min-h-14 flex-nowrap items-center justify-between p-6">
          <span className="font-mono text-[11px] tracking-[0.16em] text-fg-dim lg:hidden">COGNITRIX</span>
          <span className="lg:hidden" />
          <ThemeToggle className="ml-auto" />
        </header>

        <div className="flex flex-1 items-center px-6 pb-16">
          <div className="w-full max-w-sm mx-auto lg:mx-0 lg:ml-16 animate-rise">
            <p className="font-mono text-[11px] tracking-[0.18em] text-accent-ink">{t('login.kicker')}</p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight">{t('login.title')}</h1>
            <p className="mt-2 text-fg-dim">{t('login.subtitle')}</p>

            <OAuthButtons mode="signin" />
            <form onSubmit={handleLogin} className="mt-8 space-y-5">
              <div className="space-y-1.5">
                <label htmlFor="email" className="block font-mono text-[11px] tracking-[0.12em] text-fg-dim">{t('auth.email')}</label>
                <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com" required autoComplete="email" autoFocus />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="password" className="block font-mono text-[11px] tracking-[0.12em] text-fg-dim">{t('auth.password')}</label>
                <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••" required autoComplete="current-password" />
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

            <details className="mt-5 text-sm text-fg-dim">
              <summary className="cursor-pointer select-none hover:text-fg">{t('login.forgot')}</summary>
              {options.password_reset_by_email ? (
                forgotState === 'sent' ? (
                  <p role="status" className="mt-2 leading-relaxed">
                    {t('login.forgot.sent')}
                  </p>
                ) : (
                  <form onSubmit={sendReset} className="mt-2 space-y-2" aria-label="Reset password by email">
                    <Input type="email" required aria-label="Your account email" placeholder="you@example.com"
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
            </details>

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
