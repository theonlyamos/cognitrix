import { useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Field } from '@/components/form';
import {
  addCredential,
  deleteCredential,
  listCredentials,
  updateCredential,
  type Credential,
} from '@/lib/credentials';
import { tr } from '@/lib/i18n';

interface Draft {
  id: string | null;
  name: string;
  kind: 'token' | 'basic';
  description: string;
  hosts: string;
  header: string;
  value: string;
  username: string;
  password: string;
}

const EMPTY: Draft = {
  id: null, name: '', kind: 'token', description: '', hosts: '', header: '', value: '', username: '', password: '',
};

function draftFrom(c: Credential): Draft {
  return {
    id: c.id,
    name: c.name,
    kind: c.kind === 'basic' ? 'basic' : 'token',
    description: c.description,
    hosts: c.allowed_hosts.join(', '),
    header: c.header,
    value: '',
    username: c.username,
    password: '',
  };
}

const splitHosts = (text: string) => text.split(/[\s,]+/).map((h) => h.trim()).filter(Boolean);

/** Settings › Saved sign-ins: tokens and passwords agents use by name.
 *  The model only sees the name; the value is added when a request goes to an allowed host. */
export function CredentialsCard() {
  const [items, setItems] = useState<Credential[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    listCredentials()
      .then(setItems)
      .catch((e) => setLoadError(errorMessage(e, tr('ui.credentialsCard.couldNotLoadSavedSign'))));

  useEffect(() => { void load(); }, []);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!items) return <p className="text-[13px] text-fg-dim">{tr('ui.credentialsCard.loading')}</p>;

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    const common = {
      description: draft.description,
      allowed_hosts: splitHosts(draft.hosts),
      header: draft.kind === 'token' ? draft.header.trim() : '',
    };
    try {
      if (draft.id) {
        await updateCredential(draft.id, {
          ...common,
          ...(draft.kind === 'token'
            ? (draft.value ? { value: draft.value } : {})
            : { username: draft.username, ...(draft.password ? { password: draft.password } : {}) }),
        });
      } else {
        await addCredential({
          name: draft.name.trim(),
          kind: draft.kind,
          ...common,
          ...(draft.kind === 'token' ? { value: draft.value } : { username: draft.username, password: draft.password }),
        });
      }
      setDraft(null);
      await load();
    } catch (e) {
      setError(errorMessage(e, tr('ui.credentialsCard.couldNotSaveTheSign')));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (c: Credential) => {
    setBusy(true);
    setError(null);
    try {
      await deleteCredential(c.id);
      await load();
    } catch (e) {
      setError(errorMessage(e, tr('ui.credentialsCard.couldNotRemoveTheSign')));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[13px] leading-relaxed text-fg-dim">
        {tr('ui.credentialsCard.saveApiTokensAndPasswords')}{' '}<span className="font-mono text-[12px]">{'{{secret:NAME}}'}</span>{' '}{tr('ui.credentialsCard.inARequestOrPicks')}
      </p>

      {items.length === 0 ? (
        <p className="rounded-[10px] border border-line bg-bg px-3 py-3 text-[12.5px] text-fg-dim">{tr('ui.credentialsCard.noSavedSignInsYet')}</p>
      ) : (
        <ul className="divide-y divide-line rounded-[10px] border border-line bg-bg">
          {items.map((c) => (
            <li key={c.id} className="px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[13px]">{c.name}</span>
                <span className="rounded border border-line px-1.5 font-mono text-[10.5px] text-fg-dim">
                  {c.kind === 'basic' ? tr('ui.credentialsCard.usernamePassword') : c.kind}
                </span>
                {c.hint && <span className="font-mono text-[11.5px] text-fg-dim">{c.hint}</span>}
                <span className="flex-1" />
                <button type="button" onClick={() => { setError(null); setDraft(draftFrom(c)); }}
                  className="text-[12.5px] text-fg-dim hover:text-fg">
                  {tr('ui.credentialsCard.edit')}
                </button>
                <button type="button" onClick={() => void remove(c)} disabled={busy}
                  className="text-[12.5px] text-danger-ink hover:underline disabled:opacity-60">
                  {tr('ui.credentialsCard.remove')}
                </button>
              </div>
              <p className="mt-1 text-[12px] text-fg-dim">
                {c.allowed_hosts.length === 1 && c.allowed_hosts[0] === 'sandbox.invalid'
                  ? tr('ui.credentialsCard.onlyForCommandsInThe')
                  : tr('ui.credentialsCard.onlySentToFilter', { filter: c.allowed_hosts.filter((h) => h !== 'sandbox.invalid').join(', ') })}
                {c.last_used_at ? tr('ui.credentialsCard.lastUsedLastUsedAt', { last_used_at: c.last_used_at }) : tr('ui.credentialsCard.notUsedYet')}
              </p>
              {c.description && <p className="mt-0.5 text-[12px] text-fg-dim">{c.description}</p>}
            </li>
          ))}
        </ul>
      )}

      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}

      {draft ? (
        <CredentialForm draft={draft} setDraft={setDraft} busy={busy} onSave={() => void save()}
          onCancel={() => { setDraft(null); setError(null); }} />
      ) : (
        <button type="button" onClick={() => { setError(null); setDraft({ ...EMPTY }); }}
          className="h-9 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
          {tr('ui.credentialsCard.addSignIn')}
        </button>
      )}
    </div>
  );
}

function CredentialForm({ draft, setDraft, busy, onSave, onCancel }: {
  draft: Draft;
  setDraft: (d: Draft) => void;
  busy: boolean;
  onSave: () => void;
  onCancel: () => void;
}) {
  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });
  const editing = Boolean(draft.id);
  return (
    <form
      aria-label={editing ? tr('ui.credentialsCard.editName', { name: draft.name }) : tr('ui.credentialsCard.addSignIn')}
      className="space-y-3 rounded-[10px] border border-line bg-bg p-3"
      onSubmit={(e) => { e.preventDefault(); onSave(); }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={tr('ui.credentialsCard.name')} hint={tr('ui.credentialsCard.whatAgentsCallIt')} required>
          <Input value={draft.name} disabled={editing} onChange={(e) => set({ name: e.target.value })} placeholder="GITHUB_TOKEN" />
        </Field>
        <Field label={tr('ui.credentialsCard.type')}>
          <Select value={draft.kind} disabled={editing} onChange={(e) => set({ kind: e.target.value as Draft['kind'] })}>
            <option value="token">{tr('ui.credentialsCard.tokenOrApiKey')}</option>
            <option value="basic">{tr('ui.credentialsCard.usernameAndPassword')}</option>
          </Select>
        </Field>
      </div>
      <Field label={tr('ui.credentialsCard.onlySendTo')} hint={tr('ui.credentialsCard.hostsSeparatedByCommasExample')} required>
        <Input value={draft.hosts} onChange={(e) => set({ hosts: e.target.value })} placeholder="api.github.com" />
      </Field>
      {draft.kind === 'token' ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={tr('ui.credentialsCard.token')} hint={editing ? tr('ui.credentialsCard.leaveBlankToKeep') : tr('ui.credentialsCard.neverShownAgain')} required={!editing}>
            <Input type="password" autoComplete="off" value={draft.value}
              onChange={(e) => set({ value: e.target.value })} placeholder={editing ? tr('ui.credentialsCard.saved') : 'ghp_…'} />
          </Field>
          <Field label={tr('ui.credentialsCard.header')} hint={tr('ui.credentialsCard.blankAuthorizationBearer')}>
            <Input value={draft.header} onChange={(e) => set({ header: e.target.value })} placeholder="X-API-Key" />
          </Field>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={tr('ui.credentialsCard.username')} required>
            <Input autoComplete="off" value={draft.username} onChange={(e) => set({ username: e.target.value })} />
          </Field>
          <Field label={tr('ui.credentialsCard.password')} hint={editing ? tr('ui.credentialsCard.leaveBlankToKeep') : tr('ui.credentialsCard.neverShownAgain')} required={!editing}>
            <Input type="password" autoComplete="new-password" value={draft.password}
              onChange={(e) => set({ password: e.target.value })} placeholder={editing ? tr('ui.credentialsCard.saved') : ''} />
          </Field>
        </div>
      )}
      <Field label={tr('ui.credentialsCard.description')} hint={tr('ui.credentialsCard.tellsAgentsWhenToUse')}>
        <Input value={draft.description} onChange={(e) => set({ description: e.target.value })} placeholder={tr('ui.credentialsCard.githubApiReadAndWrite')} />
      </Field>
      <div className="flex gap-2">
        <button type="submit" disabled={busy}
          className="h-9 rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60">
          {busy ? tr('ui.credentialsCard.saving') : editing ? tr('ui.credentialsCard.save') : tr('ui.credentialsCard.add')}
        </button>
        <button type="button" onClick={onCancel} className="h-9 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
          {tr('ui.credentialsCard.cancel')}
        </button>
      </div>
    </form>
  );
}
