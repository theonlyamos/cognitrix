import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Field } from '@/components/form';
import { ThemeToggle } from '@/components/ThemeToggle';

/** Where the emailed reset link lands: pick a new password. */
export default function ResetPassword() {
  const token = useSearchParams()[0].get('token') || '';
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (password.length < 8) return setError('Password must be at least 8 characters.');
    if (password !== confirm) return setError('Passwords do not match.');
    setSaving(true);
    try {
      await api.post('/auth/reset', { token, new_password: password });
      setDone(true);
    } catch (err) {
      setError(errorMessage(err, 'Could not change the password.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      <header className="flex min-h-14 items-center justify-between p-6">
        <span className="font-mono text-[11px] tracking-[0.16em] text-fg-dim">COGNITRIX</span>
        <ThemeToggle className="ml-auto" />
      </header>
      <main className="flex flex-1 items-center px-6 pb-16">
        <div className="mx-auto w-full max-w-sm animate-rise">
          <p className="font-mono text-[11px] tracking-[0.18em] text-accent-ink">RESET PASSWORD</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">Choose a new password</h1>
          {done ? (
            <>
              <p role="status" className="mt-4 text-fg-dim">Your password was changed, and other devices were signed out.</p>
              <Link to="/" className="mt-6 inline-block font-medium text-accent-ink underline-offset-4 hover:underline">Sign in</Link>
            </>
          ) : !token ? (
            <>
              <p role="alert" className="mt-4 text-fg-dim">This link is missing its code. Open the link from the email again, or ask for a new one.</p>
              <Link to="/" className="mt-6 inline-block font-medium text-accent-ink underline-offset-4 hover:underline">Back to sign in</Link>
            </>
          ) : (
            <form onSubmit={submit} className="mt-8 space-y-4">
              <Field label="NEW PASSWORD" hint="at least 8 characters">
                <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="new-password" autoFocus />
              </Field>
              <Field label="CONFIRM">
                <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required autoComplete="new-password" />
              </Field>
              {error && <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>}
              <Button type="submit" disabled={saving} className="w-full">{saving ? 'Saving…' : 'Save new password'}</Button>
              <p className="text-sm text-fg-dim">
                <Link to="/" className="text-accent-ink underline-offset-4 hover:underline">Back to sign in</Link>
              </p>
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
