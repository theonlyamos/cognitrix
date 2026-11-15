import { useState, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { setToken } from '@/lib/session';
import { SettingRow, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';

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
    if (next.length < 8) return setError(tr('ui.settings.passwordChange.useAtLeast8Characters'));
    if (next !== repeat) return setError(tr('ui.settings.passwordChange.theTwoNewPasswordsAre'));
    setSaving(true);
    setError('');
    try {
      const { data } = await api.post('/auth/password', { current_password: current, new_password: next });
      if (data?.access_token) setToken(data.access_token);
      close();
      setDone(true);
    } catch (err) {
      setError(errorMessage(err, tr('ui.settings.passwordChange.couldNotChangeThePassword')));
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    return (
      <SettingRow
        label={tr('ui.settings.passwordChange.password')}
        detail={done ? tr('ui.settings.passwordChange.passwordChangedOtherDevicesWere') : tr('ui.settings.passwordChange.changingItSignsOutYour')}
      >
        <button type="button" className={buttonClass} onClick={() => { setDone(false); setOpen(true); }}>{tr('ui.settings.passwordChange.change')}</button>
      </SettingRow>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-2.5 px-3.5 py-3" aria-label={tr('ui.settings.passwordChange.changePassword')}>
      <div className="text-[13.5px] font-medium">{tr('ui.settings.passwordChange.changePassword')}</div>
      <label className="block text-[12px] text-fg-dim">
        {tr('ui.settings.passwordChange.currentPassword')}
        <input type="password" autoComplete="current-password" required autoFocus
          value={current} onChange={(e) => setCurrent(e.target.value)} className={`${inputClass} mt-1`} />
      </label>
      <label className="block text-[12px] text-fg-dim">
        {tr('ui.settings.passwordChange.newPassword')}
        <input type="password" autoComplete="new-password" required minLength={8}
          value={next} onChange={(e) => setNext(e.target.value)} className={`${inputClass} mt-1`} />
      </label>
      <label className="block text-[12px] text-fg-dim">
        {tr('ui.settings.passwordChange.repeatNewPassword')}
        <input type="password" autoComplete="new-password" required
          value={repeat} onChange={(e) => setRepeat(e.target.value)} className={`${inputClass} mt-1`} />
      </label>
      {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
      <div className="flex gap-2 pt-1">
        <button type="submit" disabled={saving} className={primaryButtonClass}>{saving ? tr('ui.settings.passwordChange.saving') : tr('ui.settings.passwordChange.savePassword')}</button>
        <button type="button" onClick={close} className={quietButtonClass}>{tr('ui.settings.passwordChange.cancel')}</button>
      </div>
    </form>
  );
}
