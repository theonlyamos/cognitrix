import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { CapMeter, capLines, formatCost, formatTokens, type CapStatus } from '@/components/settings/Usage';
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

  const setCap = async (person: Person, body: CapBody) => {
    setBusy(person.id);
    setError('');
    try {
      const { data } = await api.put(`/admin/users/${person.id}/spending-cap`, body);
      setPeople((list) => (list ?? []).map((p) => (p.id === person.id ? data : p)));
    } catch (err) {
      setError(errorMessage(err, tr('ui.settings.people.couldNotSaveTheSpending')));
    } finally {
      setBusy('');
    }
  };

  return (
    <>
      {error && <p role="alert" className="mb-3 text-[12.5px] text-danger-ink">{error}</p>}
      {notice && <p role="status" className="mb-3 text-[12.5px] text-fg-dim">{notice}</p>}
      <SubHeading>{tr('ui.settings.people.people')}</SubHeading>
      {!people ? (
        !error && <p className="text-[13px] text-fg-dim">{tr('ui.settings.people.loading')}</p>
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

      <SubHeading className="mt-7">{tr('ui.settings.people.defaultSpendingLimit_deb8')}</SubHeading>
      <DefaultCap onSaved={load} />

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

interface CapBody { mode: 'default' | 'custom' | 'none'; monthly_cost: number; monthly_tokens: number }

function spendingLine(person: Person): string | null {
  const s = person.spending;
  if (!s) return null;
  if (s.cap.source === 'none') return s.mode === 'none' ? tr('ui.settings.people.noSpendingLimit') : null;
  const where = s.cap.source === 'default' ? ' (default)' : '';
  return tr('ui.settings.people.thisMonthCaplinesWhereValue', { capLines: capLines(s).join(' · '), where, value: s.reached ? ' · limit reached' : '' });
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
            {spending && (
              <span className={`block ${person.spending?.reached ? 'text-danger-ink' : ''}`}>{spending}</span>
            )}
            {person.spending && person.spending.cap.source !== 'none' && <CapMeter state={person.spending} />}
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
        <button type="button" className={quietButtonClass} disabled={busy} onClick={() => setSettingSpend((v) => !v)}>
          {tr('ui.settings.people.spendingLimit')}
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
            <button
              type="button"
              className={`${quietButtonClass} text-danger-ink hover:text-danger-ink`}
              disabled={busy}
              onClick={() => setRemoving(true)}
            >
              {tr('ui.settings.people.remove')}
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
    <form onSubmit={save} className="flex flex-wrap items-end gap-2 px-3.5 pb-3" aria-label={tr('ui.settings.people.spendingLimitForName', { name: person.name })}>
      <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
        {tr('ui.settings.people.limit')}
        <select value={mode} onChange={(e) => setMode(e.target.value as CapBody['mode'])} className={inputClass}>
          <option value="default">{person.role === 'admin' ? tr('ui.settings.people.defaultAdminsHaveNone') : tr('ui.settings.people.defaultForMembers')}</option>
          <option value="custom">{tr('ui.settings.people.theirOwnLimit')}</option>
          <option value="none">{tr('ui.settings.people.noLimit')}</option>
        </select>
      </label>
      {mode === 'custom' && (
        <>
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            {tr('ui.settings.people.dollarsAMonth')}
            <input type="number" min={0} step="0.01" inputMode="decimal" placeholder={tr('ui.settings.people.none')} value={cost} onChange={(e) => setCost(e.target.value)} className={`${inputClass} w-32`} />
          </label>
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            {tr('ui.settings.people.tokensAMonth')}
            <input type="number" min={0} step="1000" inputMode="numeric" placeholder={tr('ui.settings.people.none')} value={tokens} onChange={(e) => setTokens(e.target.value)} className={`${inputClass} w-36`} />
          </label>
        </>
      )}
      <button type="submit" disabled={busy} className={primaryButtonClass}>{tr('ui.settings.people.save')}</button>
      <button type="button" className={quietButtonClass} onClick={onCancel}>{tr('ui.settings.people.cancel')}</button>
      <p className="basis-full text-[12px] text-fg-dim">
        {tr('ui.settings.people.countsEverythingTheirAgentsDo')}
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
      setError(errorMessage(err, tr('ui.settings.people.couldNotSaveTheDefault')));
      setState('idle');
    }
  };

  return (
    <div className="rounded-[12px] border border-line bg-panel p-3.5">
      <p className="mb-2.5 text-[12.5px] leading-relaxed text-fg-dim">
        {tr('ui.settings.people.appliesToEveryMemberWho')}
      </p>
      <form onSubmit={save} className="flex flex-wrap items-end gap-2" aria-label={tr('ui.settings.people.defaultSpendingLimit')}>
        <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
          {tr('ui.settings.people.dollarsAMonth')}
          <input type="number" min={0} step="0.01" inputMode="decimal" placeholder={tr('ui.settings.people.none')} value={cost} onChange={(e) => { setCost(e.target.value); setState('idle'); }} className={`${inputClass} w-32`} />
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
          {tr('ui.settings.people.tokensAMonth')}
          <input type="number" min={0} step="1000" inputMode="numeric" placeholder={tr('ui.settings.people.none')} value={tokens} onChange={(e) => { setTokens(e.target.value); setState('idle'); }} className={`${inputClass} w-36`} />
        </label>
        <button type="submit" disabled={state === 'saving'} className={primaryButtonClass}>{state === 'saving' ? tr('ui.settings.people.saving') : tr('ui.settings.people.save')}</button>
        {state === 'saved' && <span role="status" className="text-[12px] text-fg-dim">{tr('ui.settings.people.saved')}</span>}
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
      setError(errorMessage(err, tr('ui.settings.people.couldNotRemoveThisPerson')));
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
        <h2 id="remove-person-title" className="text-[15px] font-semibold">{tr('ui.settings.people.removeName', { name: person.name })}</h2>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg-dim">
          {tr('ui.settings.people.theirAccountIsDeletedAnd')}
        </p>
        <fieldset className="mt-3 rounded-lg border border-line p-2.5">
          <legend className="px-1 text-[12px] text-fg-dim">
            {tr('ui.settings.people.theirWork')}{work ? ` (${work})` : preview ? ' (none)' : ''}
          </legend>
          <label className="flex items-start gap-2 py-1 text-[13px]">
            <input type="radio" name="keep-work" checked={keepWork} onChange={() => setKeepWork(true)} className="mt-[3px]" />
            <span>{tr('ui.settings.people.moveTheirAgentsTasksTeams')}</span>
          </label>
          <label className="flex items-start gap-2 py-1 text-[13px]">
            <input type="radio" name="keep-work" checked={!keepWork} onChange={() => setKeepWork(false)} className="mt-[3px]" />
            <span>{tr('ui.settings.people.deleteThemToo')}</span>
          </label>
        </fieldset>
        <label className="mt-3 flex flex-col gap-1 text-[12px] text-fg-dim">
          <span>{tr('ui.settings.people.type')}{' '}<span className="font-mono text-fg">{person.email}</span>{' '}{tr('ui.settings.people.toConfirm')}</span>
          <input
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            aria-label={tr('ui.settings.people.theirEmailAddress')}
            className={inputClass}
          />
        </label>
        {error && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={quietButtonClass} onClick={onClose}>{tr('ui.settings.people.cancel')}</button>
          <button
            type="submit"
            disabled={!matches || working}
            className="inline-flex h-9 items-center rounded-lg bg-danger px-3 text-[12.5px] font-medium text-white disabled:opacity-50 md:h-8"
          >
            {working ? tr('ui.settings.people.removing') : tr('ui.settings.people.remove')}
          </button>
        </div>
      </form>
    </div>
  );
}
