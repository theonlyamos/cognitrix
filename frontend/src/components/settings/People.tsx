import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { formatCost, formatTokens } from '@/components/settings/Usage';
import {
  Group, SettingRow, StatusPill, SubHeading, buttonClass, primaryButtonClass, quietButtonClass,
} from '@/components/settings/ui';
import { tr, dateLocale } from '@/lib/i18n';

interface Person {
  id: string;
  name: string;
  email: string;
  role: 'admin' | 'member';
  disabled: boolean;
  you: boolean;
  created: string;
  usage_30d: { calls: number; tokens: number; cost: number };
  /** Swarm caps set by an admin; 0 is the server's own limit. */
  swarm_concurrency?: number;
  swarm_max_workers?: number;
}

interface Invite {
  id: string;
  email: string | null;
  role: 'admin' | 'member';
  expires_at: number;
  state: 'open' | 'used' | 'expired';
  used_by: string | null;
}

const inputClass =
  'h-10 min-w-0 rounded-lg border border-line bg-panel-2 px-3 text-[13.5px] outline-none focus:border-accent md:h-9';

/** Everyone with an account on this server, and invitations. Admins only. */
export function PeopleSettings() {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [emailReady, setEmailReady] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  const load = useCallback(async () => {
    try {
      const [users, pending] = await Promise.all([api.get('/admin/users'), api.get('/admin/invites')]);
      setPeople(users.data.users);
      setEmailReady(Boolean(users.data.email_configured));
      setInvites(pending.data);
      setError('');
    } catch (err) {
      setError(errorMessage(err, tr('ui.settings.people.couldNotLoadThePeople')));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const change = async (person: Person, patch: Record<string, unknown>) => {
    setBusy(person.id);
    setError('');
    try {
      const { data } = await api.patch(`/admin/users/${person.id}`, patch);
      setPeople((list) => (list ?? []).map((p) => (p.id === person.id ? data : p)));
    } catch (err) {
      setError(errorMessage(err, tr('ui.settings.people.couldNotSaveThatChange')));
    } finally {
      setBusy('');
    }
  };

  return (
    <>
      {error && <p role="alert" className="mb-3 text-[12.5px] text-danger-ink">{error}</p>}
      <SubHeading>{tr('ui.settings.people.people')}</SubHeading>
      {!people ? (
        !error && <p className="text-[13px] text-fg-dim">{tr('ui.settings.people.loading')}</p>
      ) : (
        <Group>
          {people.map((person) => (
            <PersonRow key={person.id} person={person} busy={busy === person.id} onChange={(patch) => change(person, patch)} />
          ))}
        </Group>
      )}

      <SubHeading className="mt-7">{tr('ui.settings.people.invitations')}</SubHeading>
      <InviteMaker onMade={load} />
      {invites.length > 0 && (
        <Group className="mt-3">
          {invites.map((invite) => (
            <SettingRow
              key={invite.id}
              label={invite.email || tr('ui.settings.people.anyoneWithTheLink')}
              detail={invite.state === 'used'
                ? tr('ui.settings.people.usedByUsedBy', { used_by: invite.used_by ?? 'someone' })
                : `${invite.role === 'admin' ? 'Admin' : 'Member'} · ${invite.state === 'expired' ? 'expired' : 'expires'} ${new Date(invite.expires_at * 1000).toLocaleDateString(dateLocale())}`}
            >
              <StatusPill tone={invite.state === 'open' ? 'accent' : 'off'}>{invite.state}</StatusPill>
              {invite.state !== 'used' && (
                <button
                  type="button"
                  className={quietButtonClass}
                  onClick={async () => {
                    try {
                      await api.delete(`/admin/invites/${invite.id}`);
                      await load();
                    } catch (err) {
                      setError(errorMessage(err, tr('ui.settings.people.couldNotWithdrawTheInvitation')));
                    }
                  }}
                >
                  {tr('ui.settings.people.withdraw')}
                </button>
              )}
            </SettingRow>
          ))}
        </Group>
      )}

      <SubHeading className="mt-7">{tr('ui.settings.people.email')}</SubHeading>
      <EmailCheck ready={emailReady} />
    </>
  );
}

function PersonRow({ person, busy, onChange }: {
  person: Person;
  busy: boolean;
  onChange: (patch: Record<string, unknown>) => Promise<void>;
}) {
  const [settingPassword, setSettingPassword] = useState(false);
  const [password, setPassword] = useState('');
  const [saved, setSaved] = useState(false);
  const [settingCaps, setSettingCaps] = useState(false);
  const [atOnce, setAtOnce] = useState(String(person.swarm_concurrency ?? 0));
  const [perSwarm, setPerSwarm] = useState(String(person.swarm_max_workers ?? 0));
  const usage = person.usage_30d;
  const capped = Boolean(person.swarm_concurrency || person.swarm_max_workers);

  const saveCaps = async (event: FormEvent) => {
    event.preventDefault();
    await onChange({ swarm_concurrency: Number(atOnce) || 0, swarm_max_workers: Number(perSwarm) || 0 });
    setSettingCaps(false);
  };

  const savePassword = async (event: FormEvent) => {
    event.preventDefault();
    await onChange({ new_password: password });
    setPassword('');
    setSettingPassword(false);
    setSaved(true);
  };

  return (
    <div>
      <SettingRow
        label={(
          <span className="flex flex-wrap items-center gap-2">
            {person.name}
            {person.you && <span className="font-mono text-[10.5px] text-fg-dim">{tr('ui.settings.people.you')}</span>}
            {person.role === 'admin' && <StatusPill tone="accent">{tr('ui.settings.people.admin')}</StatusPill>}
            {person.disabled && <StatusPill tone="warn">{tr('ui.settings.people.disabled')}</StatusPill>}
          </span>
        )}
        detail={(
          <>
            <span className="font-mono text-[11.5px]">{person.email}</span>
            <span className="block">
              {tr('ui.settings.people.last30DaysTokensTokens', { tokens: formatTokens(usage.tokens), cost: formatCost(usage.cost), value: saved && tr('ui.settings.people.newPasswordSaved') })}
            </span>
            {capped && (
              <span className="block">
                {tr('ui.settings.people.swarmLimitsValueValue2', { value: person.swarm_concurrency ? tr('ui.settings.people.swarmConcurrencyWorkersAtOnce', { swarm_concurrency: person.swarm_concurrency }) : tr('ui.settings.people.serverLimitAtOnce'), value2: person.swarm_max_workers ? tr('ui.settings.people.swarmMaxWorkersPerSwarm', { swarm_max_workers: person.swarm_max_workers }) : tr('ui.settings.people.serverLimitPerSwarm') })}
              </span>
            )}
          </>
        )}
      >
        <button type="button" className={quietButtonClass} disabled={busy} onClick={() => { setSaved(false); setSettingPassword((v) => !v); }}>
          {tr('ui.settings.people.setPassword')}
        </button>
        <button type="button" className={quietButtonClass} disabled={busy} onClick={() => setSettingCaps((v) => !v)}>
          {tr('ui.settings.people.swarmLimits')}
        </button>
        {!person.you && (
          <>
            <button
              type="button"
              className={quietButtonClass}
              disabled={busy}
              onClick={() => void onChange({ role: person.role === 'admin' ? 'member' : 'admin' })}
            >
              {person.role === 'admin' ? tr('ui.settings.people.makeMember') : tr('ui.settings.people.makeAdmin')}
            </button>
            <button
              type="button"
              className={buttonClass}
              disabled={busy}
              onClick={() => void onChange({ disabled: !person.disabled })}
            >
              {person.disabled ? tr('ui.settings.people.enable') : tr('ui.settings.people.disable')}
            </button>
          </>
        )}
      </SettingRow>
      {settingCaps && (
        <form onSubmit={saveCaps} className="flex flex-wrap items-end gap-2 px-3.5 pb-3" aria-label={tr('ui.settings.people.swarmLimitsForName', { name: person.name })}>
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            {tr('ui.settings.people.workersAtOnce')}
            <input type="number" min={0} max={64} value={atOnce} onChange={(e) => setAtOnce(e.target.value)} className={`${inputClass} w-28`} />
          </label>
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            {tr('ui.settings.people.workersPerSwarm')}
            <input type="number" min={0} max={500} value={perSwarm} onChange={(e) => setPerSwarm(e.target.value)} className={`${inputClass} w-28`} />
          </label>
          <button type="submit" disabled={busy} className={primaryButtonClass}>{tr('ui.settings.people.save')}</button>
          <button type="button" className={quietButtonClass} onClick={() => setSettingCaps(false)}>{tr('ui.settings.people.cancel')}</button>
          <p className="basis-full text-[12px] text-fg-dim">{tr('ui.settings.people.n0LeavesTheServerS')}</p>
        </form>
      )}
      {settingPassword && (
        <form onSubmit={savePassword} className="flex flex-wrap items-center gap-2 px-3.5 pb-3" aria-label={tr('ui.settings.people.setAPasswordForName', { name: person.name })}>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            autoFocus
            placeholder={tr('ui.settings.people.newPassword8CharactersOr')}
            aria-label={tr('ui.settings.people.newPassword')}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={`${inputClass} flex-1 basis-[220px]`}
          />
          <button type="submit" disabled={busy} className={primaryButtonClass}>{tr('ui.settings.people.save')}</button>
          <button type="button" className={quietButtonClass} onClick={() => setSettingPassword(false)}>{tr('ui.settings.people.cancel')}</button>
        </form>
      )}
    </div>
  );
}

function InviteMaker({ onMade }: { onMade: () => Promise<void> }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'member' | 'admin'>('member');
  const [link, setLink] = useState('');
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState('');
  const [making, setMaking] = useState(false);

  const make = async (event: FormEvent) => {
    event.preventDefault();
    setMaking(true);
    setError('');
    setCopied(false);
    try {
      const { data } = await api.post('/admin/invites', { email: email.trim(), role });
      setLink(`${window.location.origin}${data.path}`);
      setEmail('');
      await onMade();
    } catch (err) {
      setError(errorMessage(err, tr('ui.settings.people.couldNotCreateTheInvitation')));
    } finally {
      setMaking(false);
    }
  };

  return (
    <div className="rounded-[12px] border border-line bg-panel p-3.5">
      <p className="mb-2.5 text-[12.5px] leading-relaxed text-fg-dim">
        {tr('ui.settings.people.anInvitationIsALink')}
      </p>
      <form onSubmit={make} className="flex flex-wrap items-center gap-2" aria-label={tr('ui.settings.people.inviteSomeone')}>
        <input
          type="email"
          placeholder={tr('ui.settings.people.theirEmailOptional')}
          aria-label={tr('ui.settings.people.theirEmailOptional')}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={`${inputClass} flex-1 basis-[200px]`}
        />
        <select
          aria-label={tr('ui.settings.people.role')}
          value={role}
          onChange={(e) => setRole(e.target.value as 'member' | 'admin')}
          className={inputClass}
        >
          <option value="member">{tr('ui.settings.people.member')}</option>
          <option value="admin">{tr('ui.settings.people.admin_4e7a')}</option>
        </select>
        <button type="submit" disabled={making} className={primaryButtonClass}>{making ? tr('ui.settings.people.creating') : tr('ui.settings.people.createLink')}</button>
      </form>
      {error && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{error}</p>}
      {link && (
        <div className="mt-3 rounded-lg border border-line bg-panel-2 p-2.5" role="status">
          <div className="text-[12px] text-fg-dim">{tr('ui.settings.people.sendThisLinkToThem')}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 basis-[220px] break-all font-mono text-[11.5px]">{link}</code>
            <button
              type="button"
              className={buttonClass}
              onClick={() => {
                void navigator.clipboard?.writeText(link).then(() => setCopied(true)).catch(() => {});
              }}
            >
              {copied ? tr('ui.settings.people.copied') : tr('ui.settings.people.copy')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function EmailCheck({ ready }: { ready: boolean }) {
  const [to, setTo] = useState('');
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [sending, setSending] = useState(false);

  if (!ready) {
    return (
      <p className="rounded-[12px] border border-line bg-panel p-3.5 text-[12.5px] leading-relaxed text-fg-dim">
        {tr('ui.settings.people.noEmailServiceIsSet')}{' '}<span className="font-mono">COGNITRIX_SMTP_HOST</span>,{' '}
        <span className="font-mono">COGNITRIX_SMTP_USER</span>, <span className="font-mono">COGNITRIX_SMTP_PASSWORD</span>,{' '}
        <span className="font-mono">COGNITRIX_SMTP_FROM</span>{' '}{tr('ui.settings.people.and')}{' '}<span className="font-mono">COGNITRIX_PUBLIC_URL</span>{' '}{tr('ui.settings.people.onTheServerAndRestart')}
      </p>
    );
  }

  const send = async (event: FormEvent) => {
    event.preventDefault();
    setSending(true);
    setResult(null);
    try {
      const { data } = await api.post('/admin/email-test', { to });
      setResult({ ok: true, text: data.message });
    } catch (err) {
      setResult({ ok: false, text: errorMessage(err, tr('ui.settings.people.theTestEmailCouldNot')) });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="rounded-[12px] border border-line bg-panel p-3.5">
      <p className="mb-2.5 text-[12.5px] text-fg-dim">{tr('ui.settings.people.emailIsSetUpPeople')}</p>
      <form onSubmit={send} className="flex flex-wrap items-center gap-2" aria-label={tr('ui.settings.people.sendATestEmail')}>
        <input
          type="email"
          required
          placeholder={tr('ui.settings.people.sendATestTo')}
          aria-label={tr('ui.settings.people.sendATestTo')}
          value={to}
          onChange={(e) => setTo(e.target.value)}
          className={`${inputClass} flex-1 basis-[200px]`}
        />
        <button type="submit" disabled={sending} className={buttonClass}>{sending ? tr('ui.settings.people.sending') : tr('ui.settings.people.sendTest')}</button>
      </form>
      {result && <p role={result.ok ? 'status' : 'alert'} className={`mt-2 text-[12px] ${result.ok ? 'text-fg-dim' : 'text-danger-ink'}`}>{result.text}</p>}
    </div>
  );
}
