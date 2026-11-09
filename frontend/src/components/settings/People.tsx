import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { formatCost, formatTokens } from '@/components/settings/Usage';
import {
  Group, SettingRow, StatusPill, SubHeading, buttonClass, primaryButtonClass, quietButtonClass,
} from '@/components/settings/ui';

interface Person {
  id: string;
  name: string;
  email: string;
  role: 'admin' | 'member';
  disabled: boolean;
  you: boolean;
  created: string;
  usage_30d: { calls: number; tokens: number; cost: number };
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
      setError(errorMessage(err, 'Could not load the people on this server.'));
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
      setError(errorMessage(err, 'Could not save that change.'));
    } finally {
      setBusy('');
    }
  };

  return (
    <>
      {error && <p role="alert" className="mb-3 text-[12.5px] text-danger-ink">{error}</p>}
      <SubHeading>PEOPLE</SubHeading>
      {!people ? (
        !error && <p className="text-[13px] text-fg-dim">Loading…</p>
      ) : (
        <Group>
          {people.map((person) => (
            <PersonRow key={person.id} person={person} busy={busy === person.id} onChange={(patch) => change(person, patch)} />
          ))}
        </Group>
      )}

      <SubHeading className="mt-7">INVITATIONS</SubHeading>
      <InviteMaker onMade={load} />
      {invites.length > 0 && (
        <Group className="mt-3">
          {invites.map((invite) => (
            <SettingRow
              key={invite.id}
              label={invite.email || 'Anyone with the link'}
              detail={invite.state === 'used'
                ? `Used by ${invite.used_by ?? 'someone'}`
                : `${invite.role === 'admin' ? 'Admin' : 'Member'} · ${invite.state === 'expired' ? 'expired' : 'expires'} ${new Date(invite.expires_at * 1000).toLocaleDateString()}`}
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
                      setError(errorMessage(err, 'Could not withdraw the invitation.'));
                    }
                  }}
                >
                  Withdraw
                </button>
              )}
            </SettingRow>
          ))}
        </Group>
      )}

      <SubHeading className="mt-7">EMAIL</SubHeading>
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
  const usage = person.usage_30d;

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
            {person.you && <span className="font-mono text-[10.5px] text-fg-dim">you</span>}
            {person.role === 'admin' && <StatusPill tone="accent">admin</StatusPill>}
            {person.disabled && <StatusPill tone="warn">disabled</StatusPill>}
          </span>
        )}
        detail={(
          <>
            <span className="font-mono text-[11.5px]">{person.email}</span>
            <span className="block">
              Last 30 days: {formatTokens(usage.tokens)} tokens, {formatCost(usage.cost)}
              {saved && ' · New password saved'}
            </span>
          </>
        )}
      >
        <button type="button" className={quietButtonClass} disabled={busy} onClick={() => { setSaved(false); setSettingPassword((v) => !v); }}>
          Set password
        </button>
        {!person.you && (
          <>
            <button
              type="button"
              className={quietButtonClass}
              disabled={busy}
              onClick={() => void onChange({ role: person.role === 'admin' ? 'member' : 'admin' })}
            >
              {person.role === 'admin' ? 'Make member' : 'Make admin'}
            </button>
            <button
              type="button"
              className={buttonClass}
              disabled={busy}
              onClick={() => void onChange({ disabled: !person.disabled })}
            >
              {person.disabled ? 'Enable' : 'Disable'}
            </button>
          </>
        )}
      </SettingRow>
      {settingPassword && (
        <form onSubmit={savePassword} className="flex flex-wrap items-center gap-2 px-3.5 pb-3" aria-label={`Set a password for ${person.name}`}>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            autoFocus
            placeholder="New password, 8 characters or more"
            aria-label="New password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={`${inputClass} flex-1 basis-[220px]`}
          />
          <button type="submit" disabled={busy} className={primaryButtonClass}>Save</button>
          <button type="button" className={quietButtonClass} onClick={() => setSettingPassword(false)}>Cancel</button>
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
      setError(errorMessage(err, 'Could not create the invitation.'));
    } finally {
      setMaking(false);
    }
  };

  return (
    <div className="rounded-[12px] border border-line bg-panel p-3.5">
      <p className="mb-2.5 text-[12.5px] leading-relaxed text-fg-dim">
        An invitation is a link that lets one person create an account. It works for 7 days.
      </p>
      <form onSubmit={make} className="flex flex-wrap items-center gap-2" aria-label="Invite someone">
        <input
          type="email"
          placeholder="Their email (optional)"
          aria-label="Their email (optional)"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={`${inputClass} flex-1 basis-[200px]`}
        />
        <select
          aria-label="Role"
          value={role}
          onChange={(e) => setRole(e.target.value as 'member' | 'admin')}
          className={inputClass}
        >
          <option value="member">Member</option>
          <option value="admin">Admin</option>
        </select>
        <button type="submit" disabled={making} className={primaryButtonClass}>{making ? 'Creating…' : 'Create link'}</button>
      </form>
      {error && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{error}</p>}
      {link && (
        <div className="mt-3 rounded-lg border border-line bg-panel-2 p-2.5" role="status">
          <div className="text-[12px] text-fg-dim">Send this link to them. It is shown only once.</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 basis-[220px] break-all font-mono text-[11.5px]">{link}</code>
            <button
              type="button"
              className={buttonClass}
              onClick={() => {
                void navigator.clipboard?.writeText(link).then(() => setCopied(true)).catch(() => {});
              }}
            >
              {copied ? 'Copied' : 'Copy'}
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
        No email service is set up, so people can't reset a forgotten password by email. You can still set a
        password for them above. To turn it on, set <span className="font-mono">COGNITRIX_SMTP_HOST</span>,{' '}
        <span className="font-mono">COGNITRIX_SMTP_USER</span>, <span className="font-mono">COGNITRIX_SMTP_PASSWORD</span>,{' '}
        <span className="font-mono">COGNITRIX_SMTP_FROM</span> and <span className="font-mono">COGNITRIX_PUBLIC_URL</span> on
        the server and restart it.
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
      setResult({ ok: false, text: errorMessage(err, 'The test email could not be sent.') });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="rounded-[12px] border border-line bg-panel p-3.5">
      <p className="mb-2.5 text-[12.5px] text-fg-dim">Email is set up. People can reset a forgotten password from the sign-in page.</p>
      <form onSubmit={send} className="flex flex-wrap items-center gap-2" aria-label="Send a test email">
        <input
          type="email"
          required
          placeholder="Send a test to"
          aria-label="Send a test to"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          className={`${inputClass} flex-1 basis-[200px]`}
        />
        <button type="submit" disabled={sending} className={buttonClass}>{sending ? 'Sending…' : 'Send test'}</button>
      </form>
      {result && <p role={result.ok ? 'status' : 'alert'} className={`mt-2 text-[12px] ${result.ok ? 'text-fg-dim' : 'text-danger-ink'}`}>{result.text}</p>}
    </div>
  );
}
