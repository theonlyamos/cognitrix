import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { CapMeter, capLines, formatCost, formatTokens, type CapStatus } from '@/components/settings/Usage';
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
  /** Swarm caps set by an admin; 0 is the server's own limit. */
  swarm_concurrency?: number;
  swarm_max_workers?: number;
  /** This month's spending limit and use. */
  spending?: (CapStatus & { mode: 'default' | 'custom' | 'none'; own_cost: number; own_tokens: number }) | null;
}

interface RemovalPreview { agents: number; tasks: number; teams: number; apps: number; chats: number }

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
  const [notice, setNotice] = useState('');

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

  const setCap = async (person: Person, body: CapBody) => {
    setBusy(person.id);
    setError('');
    try {
      const { data } = await api.put(`/admin/users/${person.id}/spending-cap`, body);
      setPeople((list) => (list ?? []).map((p) => (p.id === person.id ? data : p)));
    } catch (err) {
      setError(errorMessage(err, 'Could not save the spending limit.'));
    } finally {
      setBusy('');
    }
  };

  return (
    <>
      {error && <p role="alert" className="mb-3 text-[12.5px] text-danger-ink">{error}</p>}
      {notice && <p role="status" className="mb-3 text-[12.5px] text-fg-dim">{notice}</p>}
      <SubHeading>PEOPLE</SubHeading>
      {!people ? (
        !error && <p className="text-[13px] text-fg-dim">Loading…</p>
      ) : (
        <Group>
          {people.map((person) => (
            <PersonRow
              key={person.id}
              person={person}
              busy={busy === person.id}
              onChange={(patch) => change(person, patch)}
              onCap={(body) => setCap(person, body)}
              onRemoved={(message) => { setNotice(message); void load(); }}
            />
          ))}
        </Group>
      )}

      <SubHeading className="mt-7">DEFAULT SPENDING LIMIT</SubHeading>
      <DefaultCap onSaved={load} />

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

interface CapBody { mode: 'default' | 'custom' | 'none'; monthly_cost: number; monthly_tokens: number }

function spendingLine(person: Person): string | null {
  const s = person.spending;
  if (!s) return null;
  if (s.cap.source === 'none') return s.mode === 'none' ? 'No spending limit' : null;
  const where = s.cap.source === 'default' ? ' (default)' : '';
  return `This month: ${capLines(s).join(' · ')}${where}${s.reached ? ' · limit reached' : ''}`;
}

function PersonRow({ person, busy, onChange, onCap, onRemoved }: {
  person: Person;
  busy: boolean;
  onChange: (patch: Record<string, unknown>) => Promise<void>;
  onCap: (body: CapBody) => Promise<void>;
  onRemoved: (message: string) => void;
}) {
  const [settingSpend, setSettingSpend] = useState(false);
  const [removing, setRemoving] = useState(false);
  const spending = spendingLine(person);
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
            {spending && (
              <span className={`block ${person.spending?.reached ? 'text-danger-ink' : ''}`}>{spending}</span>
            )}
            {person.spending && person.spending.cap.source !== 'none' && <CapMeter state={person.spending} />}
            {capped && (
              <span className="block">
                Swarm limits: {person.swarm_concurrency ? `${person.swarm_concurrency} workers at once` : 'server limit at once'}, {person.swarm_max_workers ? `${person.swarm_max_workers} per swarm` : 'server limit per swarm'}
              </span>
            )}
          </>
        )}
      >
        <button type="button" className={quietButtonClass} disabled={busy} onClick={() => { setSaved(false); setSettingPassword((v) => !v); }}>
          Set password
        </button>
        <button type="button" className={quietButtonClass} disabled={busy} onClick={() => setSettingSpend((v) => !v)}>
          Spending limit
        </button>
        <button type="button" className={quietButtonClass} disabled={busy} onClick={() => setSettingCaps((v) => !v)}>
          Swarm limits
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
            <button
              type="button"
              className={`${quietButtonClass} text-danger-ink hover:text-danger-ink`}
              disabled={busy}
              onClick={() => setRemoving(true)}
            >
              Remove
            </button>
          </>
        )}
      </SettingRow>
      {settingSpend && (
        <CapForm
          person={person}
          busy={busy}
          onCancel={() => setSettingSpend(false)}
          onSave={async (body) => { await onCap(body); setSettingSpend(false); }}
        />
      )}
      {removing && (
        <RemoveDialog person={person} onClose={() => setRemoving(false)} onRemoved={(message) => { setRemoving(false); onRemoved(message); }} />
      )}
      {settingCaps && (
        <form onSubmit={saveCaps} className="flex flex-wrap items-end gap-2 px-3.5 pb-3" aria-label={`Swarm limits for ${person.name}`}>
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            Workers at once
            <input type="number" min={0} max={64} value={atOnce} onChange={(e) => setAtOnce(e.target.value)} className={`${inputClass} w-28`} />
          </label>
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            Workers per swarm
            <input type="number" min={0} max={500} value={perSwarm} onChange={(e) => setPerSwarm(e.target.value)} className={`${inputClass} w-28`} />
          </label>
          <button type="submit" disabled={busy} className={primaryButtonClass}>Save</button>
          <button type="button" className={quietButtonClass} onClick={() => setSettingCaps(false)}>Cancel</button>
          <p className="basis-full text-[12px] text-fg-dim">0 leaves the server's own limit. A person can set their own limits lower, never higher.</p>
        </form>
      )}
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

function CapForm({ person, busy, onSave, onCancel }: {
  person: Person;
  busy: boolean;
  onSave: (body: CapBody) => Promise<void>;
  onCancel: () => void;
}) {
  const s = person.spending;
  const [mode, setMode] = useState<CapBody['mode']>(s?.mode ?? 'default');
  const [cost, setCost] = useState(s?.own_cost ? String(s.own_cost) : '');
  const [tokens, setTokens] = useState(s?.own_tokens ? String(s.own_tokens) : '');

  const save = async (event: FormEvent) => {
    event.preventDefault();
    await onSave({ mode, monthly_cost: Number(cost) || 0, monthly_tokens: Math.round(Number(tokens) || 0) });
  };

  return (
    <form onSubmit={save} className="flex flex-wrap items-end gap-2 px-3.5 pb-3" aria-label={`Spending limit for ${person.name}`}>
      <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
        Limit
        <select value={mode} onChange={(e) => setMode(e.target.value as CapBody['mode'])} className={inputClass}>
          <option value="default">{person.role === 'admin' ? 'Default (admins have none)' : 'Default for members'}</option>
          <option value="custom">Their own limit</option>
          <option value="none">No limit</option>
        </select>
      </label>
      {mode === 'custom' && (
        <>
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            Dollars a month
            <input type="number" min={0} step="0.01" inputMode="decimal" placeholder="none" value={cost} onChange={(e) => setCost(e.target.value)} className={`${inputClass} w-32`} />
          </label>
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            Tokens a month
            <input type="number" min={0} step="1000" inputMode="numeric" placeholder="none" value={tokens} onChange={(e) => setTokens(e.target.value)} className={`${inputClass} w-36`} />
          </label>
        </>
      )}
      <button type="submit" disabled={busy} className={primaryButtonClass}>Save</button>
      <button type="button" className={quietButtonClass} onClick={onCancel}>Cancel</button>
      <p className="basis-full text-[12px] text-fg-dim">
        Counts everything their agents do, swarm workers included, for the calendar month. When it runs out, their agents stop
        using models until the 1st. Leave a box empty for no limit of that kind. Models with no known price only count toward tokens.
      </p>
    </form>
  );
}

function DefaultCap({ onSaved }: { onSaved: () => Promise<void> }) {
  const [cost, setCost] = useState('');
  const [tokens, setTokens] = useState('');
  const [state, setState] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [error, setError] = useState('');

  useEffect(() => {
    api.get('/admin/spending-cap')
      .then(({ data }) => {
        setCost(data.monthly_cost ? String(data.monthly_cost) : '');
        setTokens(data.monthly_tokens ? String(data.monthly_tokens) : '');
      })
      .catch(() => {});
  }, []);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setState('saving');
    setError('');
    try {
      await api.put('/admin/spending-cap', { monthly_cost: Number(cost) || 0, monthly_tokens: Math.round(Number(tokens) || 0) });
      setState('saved');
      await onSaved();
    } catch (err) {
      setError(errorMessage(err, 'Could not save the default limit.'));
      setState('idle');
    }
  };

  return (
    <div className="rounded-[12px] border border-line bg-panel p-3.5">
      <p className="mb-2.5 text-[12.5px] leading-relaxed text-fg-dim">
        Applies to every member who has no limit of their own. Admins are only limited by a limit set on them. Leave both empty for no default.
      </p>
      <form onSubmit={save} className="flex flex-wrap items-end gap-2" aria-label="Default spending limit">
        <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
          Dollars a month
          <input type="number" min={0} step="0.01" inputMode="decimal" placeholder="none" value={cost} onChange={(e) => { setCost(e.target.value); setState('idle'); }} className={`${inputClass} w-32`} />
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
          Tokens a month
          <input type="number" min={0} step="1000" inputMode="numeric" placeholder="none" value={tokens} onChange={(e) => { setTokens(e.target.value); setState('idle'); }} className={`${inputClass} w-36`} />
        </label>
        <button type="submit" disabled={state === 'saving'} className={primaryButtonClass}>{state === 'saving' ? 'Saving…' : 'Save'}</button>
        {state === 'saved' && <span role="status" className="text-[12px] text-fg-dim">Saved</span>}
      </form>
      {error && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{error}</p>}
    </div>
  );
}

function RemoveDialog({ person, onClose, onRemoved }: {
  person: Person;
  onClose: () => void;
  onRemoved: (message: string) => void;
}) {
  const [preview, setPreview] = useState<RemovalPreview | null>(null);
  const [keepWork, setKeepWork] = useState(true);
  const [typed, setTyped] = useState('');
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const matches = typed.trim().toLowerCase() === person.email.toLowerCase();

  useEffect(() => {
    api.get(`/admin/users/${person.id}/removal`).then(({ data }) => setPreview(data)).catch(() => {});
  }, [person.id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const remove = async (event: FormEvent) => {
    event.preventDefault();
    setWorking(true);
    setError('');
    try {
      const { data } = await api.post(`/admin/users/${person.id}/remove`, { keep_work: keepWork, confirm_email: typed.trim() });
      onRemoved(data.message);
    } catch (err) {
      setError(errorMessage(err, 'Could not remove this person.'));
      setWorking(false);
    }
  };

  const work = preview
    ? [
        [preview.agents, 'agent'], [preview.tasks, 'task'], [preview.teams, 'team'], [preview.apps, 'app'],
      ].filter(([n]) => Number(n) > 0).map(([n, word]) => `${n} ${word}${Number(n) === 1 ? '' : 's'}`).join(', ')
    : '';

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 md:items-center md:p-4" onClick={onClose}>
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="remove-person-title"
        onSubmit={remove}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-[460px] rounded-t-[16px] border border-line bg-panel p-4 shadow-xl md:rounded-[14px]"
      >
        <h2 id="remove-person-title" className="text-[15px] font-semibold">Remove {person.name}?</h2>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg-dim">
          Their account is deleted and they are signed out everywhere. Their chats, memories, keys, saved sign-ins, linked
          chat apps and connected apps are deleted. Usage totals and the audit log are kept. This can&apos;t be undone.
          To stop them for now instead, use Disable.
        </p>
        <fieldset className="mt-3 rounded-lg border border-line p-2.5">
          <legend className="px-1 text-[12px] text-fg-dim">
            Their work{work ? ` (${work})` : preview ? ' (none)' : ''}
          </legend>
          <label className="flex items-start gap-2 py-1 text-[13px]">
            <input type="radio" name="keep-work" checked={keepWork} onChange={() => setKeepWork(true)} className="mt-[3px]" />
            <span>Move their agents, tasks, teams, apps and pages to me</span>
          </label>
          <label className="flex items-start gap-2 py-1 text-[13px]">
            <input type="radio" name="keep-work" checked={!keepWork} onChange={() => setKeepWork(false)} className="mt-[3px]" />
            <span>Delete them too</span>
          </label>
        </fieldset>
        <label className="mt-3 flex flex-col gap-1 text-[12px] text-fg-dim">
          <span>Type <span className="font-mono text-fg">{person.email}</span> to confirm</span>
          <input
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            aria-label="Their email address"
            className={inputClass}
          />
        </label>
        {error && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={quietButtonClass} onClick={onClose}>Cancel</button>
          <button
            type="submit"
            disabled={!matches || working}
            className="inline-flex h-9 items-center rounded-lg bg-danger px-3 text-[12.5px] font-medium text-white disabled:opacity-50 md:h-8"
          >
            {working ? 'Removing…' : 'Remove'}
          </button>
        </div>
      </form>
    </div>
  );
}
