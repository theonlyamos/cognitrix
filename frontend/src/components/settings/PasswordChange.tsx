import { useState, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { setToken } from '@/lib/session';
import { SettingRow, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';

const inputClass =
  'h-10 w-full rounded-lg border border-line bg-panel-2 px-3 text-[13.5px] outline-none focus:border-accent md:h-9';

/** Change the signed-in person's password. Other devices are signed out. */
export function PasswordChange() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  const close = () => {
    setOpen(false);
    setCurrent('');
    setNext('');
    setRepeat('');
    setError('');
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (next.length < 8) return setError('Use at least 8 characters.');
    if (next !== repeat) return setError('The two new passwords are different.');
    setSaving(true);
    setError('');
    try {
      const { data } = await api.post('/auth/password', { current_password: current, new_password: next });
      if (data?.access_token) setToken(data.access_token);
      close();
      setDone(true);
    } catch (err) {
      setError(errorMessage(err, 'Could not change the password.'));
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    return (
      <SettingRow
        label="Password"
        detail={done ? 'Password changed. Other devices were signed out.' : 'Changing it signs out your other devices.'}
      >
        <button type="button" className={buttonClass} onClick={() => { setDone(false); setOpen(true); }}>Change</button>
      </SettingRow>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-2.5 px-3.5 py-3" aria-label="Change password">
      <div className="text-[13.5px] font-medium">Change password</div>
      <label className="block text-[12px] text-fg-dim">
        Current password
        <input type="password" autoComplete="current-password" required autoFocus
          value={current} onChange={(e) => setCurrent(e.target.value)} className={`${inputClass} mt-1`} />
      </label>
      <label className="block text-[12px] text-fg-dim">
        New password
        <input type="password" autoComplete="new-password" required minLength={8}
          value={next} onChange={(e) => setNext(e.target.value)} className={`${inputClass} mt-1`} />
      </label>
      <label className="block text-[12px] text-fg-dim">
        Repeat new password
        <input type="password" autoComplete="new-password" required
          value={repeat} onChange={(e) => setRepeat(e.target.value)} className={`${inputClass} mt-1`} />
      </label>
      {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
      <div className="flex gap-2 pt-1">
        <button type="submit" disabled={saving} className={primaryButtonClass}>{saving ? 'Saving…' : 'Save password'}</button>
        <button type="button" onClick={close} className={quietButtonClass}>Cancel</button>
      </div>
    </form>
  );
}
