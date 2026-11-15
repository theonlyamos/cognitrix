import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Field } from '@/components/form';
import { Toggle } from '@/pages/Notifications';
import { cn } from '@/lib/utils';
import {
  PRESETS,
  deleteAgentEmail,
  getAgentEmail,
  saveAgentAddress,
  saveAgentEmail,
  testAgentEmail,
  type AgentAddress,
  type AgentEmailState,
  type EmailAccount,
  type EmailUpdate,
  type Security,
} from '@/lib/agentEmail';
import { tr } from '@/lib/i18n';

interface AgentLite { id: string; name: string }

function StatusDot({ account }: { account: EmailAccount }) {
  const state = !account.enabled
    ? { text: 'Off', tone: 'bg-fg-dim' }
    : account.status === 'error'
      ? { text: 'Error', tone: 'bg-danger' }
      : account.status === 'connected'
        ? { text: 'Checking mail', tone: 'bg-accent' }
        : { text: 'Connecting', tone: 'bg-fg-dim animate-pulse' };
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-fg-dim">
      <span className={cn('h-1.5 w-1.5 rounded-full', state.tone)} aria-hidden />
      {state.text}
    </span>
  );
}

const SECURITY: { value: Security; label: string }[] = [
  { value: 'ssl', label: 'SSL/TLS' },
  { value: 'starttls', label: 'STARTTLS' },
  { value: 'none', get label() { return tr('ui.agentEmailCard.none'); } },
];

/** Mailbox address, sign-in and servers. Used to connect and to change them later. */
function MailboxForm({
  account, onSaved, onCancel,
}: {
  account: EmailAccount | null;
  onSaved: (next: AgentEmailState) => void;
  onCancel?: () => void;
}) {
  const [preset, setPreset] = useState(account ? 'other' : 'gmail');
  const [form, setForm] = useState({
    address: account?.address ?? '',
    username: account?.username ?? '',
    password: '',
    addressing: account?.addressing ?? 'plus',
    imap_host: account?.imap_host ?? PRESETS[0].imap_host,
    imap_port: account?.imap_port ?? PRESETS[0].imap_port,
    imap_security: account?.imap_security ?? PRESETS[0].imap_security,
    smtp_host: account?.smtp_host ?? PRESETS[0].smtp_host,
    smtp_port: account?.smtp_port ?? PRESETS[0].smtp_port,
    smtp_security: account?.smtp_security ?? PRESETS[0].smtp_security,
  });
  const [showServers, setShowServers] = useState(!!account);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const note = PRESETS.find((p) => p.id === preset)?.note;
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));

  const pick = (id: string) => {
    setPreset(id);
    const p = PRESETS.find((x) => x.id === id);
    if (!p) return;
    if (id === 'other') setShowServers(true);
    else set({
      imap_host: p.imap_host, imap_port: p.imap_port, imap_security: p.imap_security,
      smtp_host: p.smtp_host, smtp_port: p.smtp_port, smtp_security: p.smtp_security,
    });
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    const body: EmailUpdate = {
      address: form.address.trim(), username: form.username.trim(), addressing: form.addressing,
      imap_host: form.imap_host.trim(), imap_port: Number(form.imap_port), imap_security: form.imap_security,
      smtp_host: form.smtp_host.trim(), smtp_port: Number(form.smtp_port), smtp_security: form.smtp_security,
    };
    if (form.password) body.password = form.password;
    try {
      onSaved(await saveAgentEmail(body));
    } catch (e) {
      setError(errorMessage(e, tr('ui.agentEmailCard.couldNotConnectTheMailbox')));
    } finally {
      setBusy(false);
    }
  };

  const missing = !form.address.trim() || !form.imap_host.trim() || !form.smtp_host.trim() || (!form.password && !account?.password_set);

  return (
    <form className="space-y-2.5" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <Field label={tr('ui.agentEmailCard.provider')}>
        <Select value={preset} onChange={(e) => pick(e.target.value)} disabled={busy} aria-label={tr('ui.agentEmailCard.mailProvider')}>
          {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </Select>
      </Field>
      {note && <p className="text-[12px] leading-snug text-fg-dim">{note}</p>}
      <Field label={tr('ui.agentEmailCard.mailboxAddress')} hint={tr('ui.agentEmailCard.aMailboxJustForYour')}>
        <Input type="email" autoComplete="off" placeholder="agents@example.com" value={form.address}
          onChange={(e) => set({ address: e.target.value })} disabled={busy} aria-label={tr('ui.agentEmailCard.mailboxAddress_caaa')} />
      </Field>
      <Field label={tr('ui.agentEmailCard.password')} hint={tr('ui.agentEmailCard.anAppPasswordWhereYour')}>
        <Input type="password" autoComplete="new-password" value={form.password}
          placeholder={account?.password_set ? tr('ui.agentEmailCard.savedLeaveEmptyToKeep') : ''}
          onChange={(e) => set({ password: e.target.value })} disabled={busy} aria-label={tr('ui.agentEmailCard.mailboxPassword')} />
      </Field>
      <fieldset className="space-y-1">
        <legend className="mb-1 font-mono text-[11px] text-fg-dim">{tr('ui.agentEmailCard.agentAddresses')}</legend>
        {(['plus', 'domain'] as const).map((value) => (
          <label key={value} className="flex items-start gap-2 text-[12.5px]">
            <input type="radio" name="addressing" className="mt-[3px]" checked={form.addressing === value}
              onChange={() => set({ addressing: value })} disabled={busy} />
            <span>
              {value === 'plus'
                ? <>{tr('ui.agentEmailCard.plusAddressesLike')}{' '}<span className="font-mono">{(form.address.split('@')[0] || tr('ui.agentEmailCard.agents'))}+researcher@{form.address.split('@')[1] || 'example.com'}</span>{tr('ui.agentEmailCard.worksWithGmailAndMost')}</>
                : <>{tr('ui.agentEmailCard.ownAddressesLike')}{' '}<span className="font-mono">researcher@{form.address.split('@')[1] || 'example.com'}</span>{tr('ui.agentEmailCard.needsAllOfTheDomain')}</>}
            </span>
          </label>
        ))}
      </fieldset>
      <button type="button" className="text-[12.5px] text-accent-ink hover:underline" aria-expanded={showServers}
        onClick={() => setShowServers((v) => !v)}>
        {showServers ? tr('ui.agentEmailCard.hideServerDetails') : tr('ui.agentEmailCard.serverDetails')}
      </button>
      {showServers && (
        <div className="space-y-2 rounded-[10px] border border-line bg-bg p-3">
          <Field label={tr('ui.agentEmailCard.userName')} hint={tr('ui.agentEmailCard.leaveEmptyToUseThe')}>
            <Input autoComplete="off" value={form.username} onChange={(e) => set({ username: e.target.value })}
              disabled={busy} aria-label={tr('ui.agentEmailCard.userName_542f')} />
          </Field>
          {(['imap', 'smtp'] as const).map((kind) => (
            <div key={kind} className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_90px_120px]">
              <Field label={kind === 'imap' ? tr('ui.agentEmailCard.imapServerReading') : tr('ui.agentEmailCard.smtpServerSending')}>
                <Input autoComplete="off" value={form[`${kind}_host`]} aria-label={tr('ui.agentEmailCard.kindServer', { kind: kind.toUpperCase() })}
                  onChange={(e) => set({ [`${kind}_host`]: e.target.value } as Partial<typeof form>)} disabled={busy} />
              </Field>
              <Field label={tr('ui.agentEmailCard.port')}>
                <Input inputMode="numeric" value={String(form[`${kind}_port`])} aria-label={tr('ui.agentEmailCard.kindPort', { kind: kind.toUpperCase() })}
                  onChange={(e) => set({ [`${kind}_port`]: Number(e.target.value.replace(/\D/g, '')) || 0 } as Partial<typeof form>)} disabled={busy} />
              </Field>
              <Field label={tr('ui.agentEmailCard.security')}>
                <Select value={form[`${kind}_security`]} aria-label={tr('ui.agentEmailCard.kindSecurity', { kind: kind.toUpperCase() })}
                  onChange={(e) => set({ [`${kind}_security`]: e.target.value as Security } as Partial<typeof form>)} disabled={busy}>
                  {SECURITY.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </Select>
              </Field>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-2 pt-1">
        <button type="submit" disabled={busy || missing}
          className="h-10 rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-foreground disabled:opacity-60">
          {busy ? tr('ui.agentEmailCard.checking') : account ? tr('ui.agentEmailCard.saveMailbox') : tr('ui.agentEmailCard.connectMailbox')}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="h-10 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
            {tr('ui.agentEmailCard.cancel')}
          </button>
        )}
      </div>
      <div aria-live="polite" className="min-h-[1rem] text-[12px]">
        {error && <span role="alert" className="text-danger-ink">{error}</span>}
      </div>
    </form>
  );
}

function AddressRow({ row, busy, onSave }: {
  row: AgentAddress;
  busy: boolean;
  onSave: (update: { name?: string; enabled?: boolean }) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(row.name);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(row.address);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked: the address can be selected by hand */ }
  };
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3.5 py-2.5">
      <div className="min-w-0 flex-1 basis-[200px]">
        <div className="text-[13px] font-medium">{row.agent_name}</div>
        {editing ? (
          <form className="mt-1 flex flex-wrap items-center gap-2" onSubmit={async (e) => {
            e.preventDefault();
            if (await onSave({ name: name.trim() })) setEditing(false);
          }}>
            <Input value={name} onChange={(e) => setName(e.target.value)} aria-label={tr('ui.agentEmailCard.addressNameForAgentName', { agent_name: row.agent_name })}
              className="h-8 w-44 font-mono text-[12px]" disabled={busy} autoFocus />
            <button type="submit" disabled={busy || !name.trim()} className="text-[12.5px] text-accent-ink hover:underline">{tr('ui.agentEmailCard.save')}</button>
            <button type="button" onClick={() => { setEditing(false); setName(row.name); }} className="text-[12.5px] text-fg-dim hover:underline">{tr('ui.agentEmailCard.cancel')}</button>
          </form>
        ) : (
          <div className={cn('mt-0.5 flex flex-wrap items-center gap-x-3 text-[12px]', !row.enabled && 'opacity-60')}>
            <span className="select-all break-all font-mono" aria-label={tr('ui.agentEmailCard.agentNameEmailAddress', { agent_name: row.agent_name })}>{row.address}</span>
            <button type="button" onClick={copy} className="text-accent-ink hover:underline">{copied ? tr('ui.agentEmailCard.copied') : tr('ui.agentEmailCard.copy')}</button>
            <button type="button" onClick={() => setEditing(true)} className="text-fg-dim hover:text-fg hover:underline">{tr('ui.agentEmailCard.rename')}</button>
          </div>
        )}
      </div>
      <Toggle label={tr('ui.agentEmailCard.agentNameEmailOn', { agent_name: row.agent_name })} checked={row.enabled} disabled={busy}
        onChange={(v) => void onSave({ enabled: v })} />
    </div>
  );
}

/** Settings › Agent email: connect a mailbox and every agent gets its own
 *  address. Mail to it starts a chat with that agent; it answers from it. */
export function AgentEmailCard() {
  const [state, setState] = useState<AgentEmailState | null>(null);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [senders, setSenders] = useState('');

  useEffect(() => {
    getAgentEmail().then((s) => { setState(s); setSenders(s.account?.allowed_senders ?? ''); })
      .catch((e) => setLoadError(errorMessage(e, tr('ui.agentEmailCard.couldNotLoadAgentEmail'))));
    api.get<AgentLite[]>('/agents').then((r) => setAgents(r.data ?? [])).catch(() => {});
  }, []);

  // Show the first check's result once the mailbox is connected.
  const waiting = !!state?.account?.enabled && !state.account.status;
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => { void getAgentEmail().then(setState).catch(() => {}); }, 4000);
    return () => clearInterval(timer);
  }, [waiting]);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!state) return <p className="text-[13px] text-fg-dim">{tr('ui.agentEmailCard.loading')}</p>;

  const run = async (work: () => Promise<AgentEmailState | null | void>, done?: string) => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const next = await work();
      if (next) { setState(next); setSenders(next.account?.allowed_senders ?? ''); }
      if (done) setNote(done);
      return true;
    } catch (e) {
      setError(errorMessage(e, tr('ui.agentEmailCard.couldNotUpdateAgentEmail')));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const update = (body: EmailUpdate, done?: string) => run(() => saveAgentEmail(body), done);
  const account = state.account;

  if (!account || editing) {
    return (
      <div className="space-y-3">
        {!state.available && (
          <p className="rounded-[10px] border border-line bg-bg px-3 py-2 text-[12.5px] text-fg-dim">
            {tr('ui.agentEmailCard.agentEmailIsTurnedOff')}<span className="font-mono">COGNITRIX_EMAIL=0</span>).
          </p>
        )}
        {!account && (
          <p className="text-[12.5px] leading-relaxed text-fg-dim">
            {tr('ui.agentEmailCard.connectAMailboxAndEach')}
          </p>
        )}
        <div className="rounded-[12px] border border-line bg-panel p-3.5">
          <MailboxForm account={account} onCancel={account ? () => setEditing(false) : undefined}
            onSaved={(next) => { setState(next); setSenders(next.account?.allowed_senders ?? ''); setEditing(false); }} />
        </div>
      </div>
    );
  }

  const base = account.address;
  return (
    <div className="space-y-4">
      <div className="rounded-[12px] border border-line bg-panel px-3.5 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="break-all font-mono text-[13px]">{base}</div>
            <div className="mt-0.5"><StatusDot account={account} /></div>
          </div>
          <Toggle label={tr('ui.agentEmailCard.agentEmailOn')} checked={account.enabled} disabled={busy}
            onChange={(v) => void update({ enabled: v })} />
        </div>
        {account.enabled && account.status === 'error' && account.last_error && (
          <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{account.last_error}</p>
        )}
      </div>

      <div>
        <h3 className="mb-1.5 font-mono text-[11px] text-fg-dim">{tr('ui.agentEmailCard.agentAddresses')}</h3>
        <div className="divide-y divide-line rounded-[12px] border border-line bg-panel">
          {state.agents.length === 0 && <p className="px-3.5 py-3 text-[12.5px] text-fg-dim">{tr('ui.agentEmailCard.youHaveNoAgentsYet')}</p>}
          {state.agents.map((row) => (
            <AddressRow key={row.agent_id} row={row} busy={busy}
              onSave={(u) => run(() => saveAgentAddress(row.agent_id, u), u.name ? tr('ui.agentEmailCard.addressSaved') : undefined)} />
          ))}
        </div>
        <div className="mt-3">
          <Field label={tr('ui.agentEmailCard.mailToBaseGoesTo', { base: base.toUpperCase() })}>
            <Select value={account.agent_id} disabled={busy} aria-label={tr('ui.agentEmailCard.agentForTheMailboxAddress')}
              onChange={(e) => void update({ agent_id: e.target.value }, 'Saved.')}>
              <option value="">{tr('ui.agentEmailCard.yourFirstAgent')}</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          </Field>
        </div>
      </div>

      <div>
        <h3 className="mb-1.5 font-mono text-[11px] text-fg-dim">{tr('ui.agentEmailCard.whoCanGiveAgentsWork')}</h3>
        <div className="space-y-2 rounded-[12px] border border-line bg-panel p-3.5">
          <Field label={tr('ui.agentEmailCard.allowedSenders')} hint={tr('ui.agentEmailCard.onePerLineAddressesOr', { owner_email: state.owner_email || 'your account email' })}>
            <textarea value={senders} onChange={(e) => setSenders(e.target.value)} rows={3} disabled={busy}
              placeholder={state.owner_email} aria-label={tr('ui.agentEmailCard.allowedSenders_a265')}
              className="w-full rounded-lg border border-line bg-bg px-2.5 py-2 font-mono text-[12.5px] outline-none focus:border-accent" />
          </Field>
          {senders !== account.allowed_senders && (
            <button type="button" disabled={busy} onClick={() => void update({ allowed_senders: senders }, tr('ui.agentEmailCard.sendersSaved'))}
              className="h-9 rounded-lg bg-accent px-3 text-[12.5px] font-medium text-accent-foreground disabled:opacity-60">
              {tr('ui.agentEmailCard.saveSenders')}
            </button>
          )}
          <div className="divide-y divide-line">
            <div className="flex items-center justify-between gap-4 py-2">
              <div>
                <div className="text-[13px]">{tr('ui.agentEmailCard.checkThatMailReallyComes')}</div>
                <div className="text-[12px] leading-snug text-fg-dim">
                  {tr('ui.agentEmailCard.onlyTrustMailYourMail')}
                </div>
              </div>
              <Toggle label={tr('ui.agentEmailCard.checkSenders')} checked={account.require_auth} disabled={busy}
                onChange={(v) => void update({ require_auth: v })} />
            </div>
            <div className="flex items-center justify-between gap-4 py-2">
              <div>
                <div className="text-[13px]">{tr('ui.agentEmailCard.answerOtherPeople')}</div>
                <div className="text-[12px] leading-snug text-fg-dim">
                  {tr('ui.agentEmailCard.offMailFromAnyoneElse')}
                </div>
              </div>
              <Toggle label={tr('ui.agentEmailCard.answerOtherPeopleByEmail')} checked={account.answer_others} disabled={busy}
                onChange={(v) => void update({ answer_others: v })} />
            </div>
          </div>
        </div>
      </div>

      <p className="text-[12px] leading-snug text-fg-dim">
        {tr('ui.agentEmailCard.agentsCanAlsoSendEmail')}
      </p>

      <div className="flex flex-wrap gap-2 text-[12.5px]">
        <button type="button" disabled={busy}
          onClick={() => void run(async () => { const r = await testAgentEmail(); setNote(tr('ui.agentEmailCard.testEmailSentToTo', { to: r.to })); })}
          className="h-9 rounded-lg border border-line px-3 hover:bg-panel-2 disabled:opacity-60">
          {tr('ui.agentEmailCard.sendTestEmail')}
        </button>
        <button type="button" disabled={busy} onClick={() => setEditing(true)}
          className="h-9 rounded-lg border border-line px-3 hover:bg-panel-2 disabled:opacity-60">
          {tr('ui.agentEmailCard.changeMailbox')}
        </button>
        <button type="button" disabled={busy}
          onClick={() => void run(async () => { await deleteAgentEmail(); setState({ ...state, account: null, agents: [] }); }, tr('ui.agentEmailCard.mailboxDisconnected'))}
          className="h-9 rounded-lg px-3 text-danger-ink hover:underline disabled:opacity-60">
          {tr('ui.agentEmailCard.disconnect')}
        </button>
      </div>
      <div aria-live="polite" className="min-h-[1rem] text-[12px]">
        {error ? <span role="alert" className="text-danger-ink">{error}</span> : note ? <span className="text-fg-dim">{note}</span> : null}
      </div>
    </div>
  );
}
