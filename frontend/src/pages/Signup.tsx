import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Field } from '@/components/form';
import { ThemeToggle } from '@/components/ThemeToggle';

const GRID = {
  backgroundImage:
    'linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px)',
  backgroundSize: '32px 32px',
};

export default function Signup() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!name.trim()) return setError('Enter your name.');
    if (password.length < 8) return setError('Password must be at least 8 characters.');
    if (password !== confirm) return setError('Passwords do not match.');

    setLoading(true);
    try {
      await api.post('/auth/signup', { name, email, password });
      navigate('/');
    } catch (err) {
      setError(errorMessage(err, 'Unable to create account.'));
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
            Your agents,<br />one console.
          </h2>
          <p className="mt-4 text-fg-dim max-w-sm">
            Create an account to build agents, run multi-step tasks, and coordinate teams — with tools and approval built in.
          </p>
        </div>
        <div className="relative font-mono text-[11px] text-fg-dim space-y-1.5">
          <div className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-accent" /> local-first · bring your own keys</div>
          <div className="text-fg-dim/70">encrypted · self-hosted · open source</div>
        </div>
      </aside>

      {/* Form column */}
      <main className="flex flex-1 flex-col">
        <header className="flex items-center justify-between p-6">
          <span className="font-mono text-[11px] tracking-[0.16em] text-fg-dim lg:hidden">COGNITRIX</span>
          <ThemeToggle className="ml-auto" />
        </header>

        <div className="flex flex-1 items-center px-6 pb-12">
          <div className="w-full max-w-sm mx-auto lg:mx-0 lg:ml-16 animate-rise">
            <p className="font-mono text-[11px] tracking-[0.18em] text-accent-ink">CREATE ACCOUNT</p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight">Get started</h1>
            <p className="mt-2 text-fg-dim">Set up your workspace in a few seconds.</p>

            <form onSubmit={handleSignup} className="mt-8 space-y-4">
              <Field label="NAME"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ada Lovelace" required autoComplete="name" autoFocus /></Field>
              <Field label="EMAIL"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required autoComplete="email" /></Field>
              <Field label="PASSWORD" hint="at least 8 characters"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" required autoComplete="new-password" /></Field>
              <Field label="CONFIRM"><Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="••••••••" required autoComplete="new-password" /></Field>

              {error && <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>}

              <Button type="submit" disabled={loading} className="w-full">
                {loading ? <span role="status" className="inline-flex items-center gap-2"><Spinner /> Creating…</span> : <>Create account <span aria-hidden>→</span></>}
              </Button>
            </form>

            <p className="mt-6 text-sm text-fg-dim">
              Already have an account?{' '}
              <Link to="/" className="text-accent-ink font-medium hover:underline underline-offset-4">Sign in</Link>
            </p>
          </div>
        </div>

        <footer className="px-6 pb-6 font-mono text-[10.5px] text-fg-dim lg:ml-16">
          by creating an account you agree to the terms &amp; privacy policy
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
