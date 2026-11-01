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
      .catch((e) => setLoadError(errorMessage(e, 'Could not load saved sign-ins.')));

  useEffect(() => { void load(); }, []);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!items) return <p className="text-[13px] text-fg-dim">Loading…</p>;

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
      setError(errorMessage(e, 'Could not save the sign-in.'));
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
      setError(errorMessage(e, 'Could not remove the sign-in.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[13px] leading-relaxed text-fg-dim">
        Save API tokens and passwords here so agents can sign in to services without ever seeing them. An agent only
        knows the name: it writes <span className="font-mono text-[12px]">{'{{secret:NAME}}'}</span> in a request (or picks the
        sign-in in the HTTP Request tool) and Cognitrix adds the value as the request goes out, only to the hosts you list.
        Values are encrypted on disk and hidden from anything a tool sends back. MCP server headers can use the same placeholder.
      </p>

      {items.length === 0 ? (
        <p className="rounded-[10px] border border-line bg-bg px-3 py-3 text-[12.5px] text-fg-dim">No saved sign-ins yet.</p>
      ) : (
        <ul className="divide-y divide-line rounded-[10px] border border-line bg-bg">
          {items.map((c) => (
            <li key={c.id} className="px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[13px]">{c.name}</span>
                <span className="rounded border border-line px-1.5 font-mono text-[10.5px] text-fg-dim">
                  {c.kind === 'basic' ? 'username + password' : c.kind}
                </span>
                {c.hint && <span className="font-mono text-[11.5px] text-fg-dim">{c.hint}</span>}
                <span className="flex-1" />
                <button type="button" onClick={() => { setError(null); setDraft(draftFrom(c)); }}
                  className="text-[12.5px] text-fg-dim hover:text-fg">
                  Edit
                </button>
                <button type="button" onClick={() => void remove(c)} disabled={busy}
                  className="text-[12.5px] text-danger-ink hover:underline disabled:opacity-60">
                  Remove
                </button>
              </div>
              <p className="mt-1 text-[12px] text-fg-dim">
                Only sent to {c.allowed_hosts.join(', ')}
                {c.last_used_at ? ` · last used ${c.last_used_at} UTC` : ' · not used yet'}
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
          Add sign-in
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
      aria-label={editing ? `Edit ${draft.name}` : 'Add sign-in'}
      className="space-y-3 rounded-[10px] border border-line bg-bg p-3"
      onSubmit={(e) => { e.preventDefault(); onSave(); }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="NAME" hint="what agents call it" required>
          <Input value={draft.name} disabled={editing} onChange={(e) => set({ name: e.target.value })} placeholder="GITHUB_TOKEN" />
        </Field>
        <Field label="TYPE">
          <Select value={draft.kind} disabled={editing} onChange={(e) => set({ kind: e.target.value as Draft['kind'] })}>
            <option value="token">Token or API key</option>
            <option value="basic">Username and password</option>
          </Select>
        </Field>
      </div>
      <Field label="ONLY SEND TO" hint="hosts, separated by commas; *.example.com covers subdomains" required>
        <Input value={draft.hosts} onChange={(e) => set({ hosts: e.target.value })} placeholder="api.github.com" />
      </Field>
      {draft.kind === 'token' ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="TOKEN" hint={editing ? 'leave blank to keep' : 'never shown again'} required={!editing}>
            <Input type="password" autoComplete="off" value={draft.value}
              onChange={(e) => set({ value: e.target.value })} placeholder={editing ? 'saved' : 'ghp_…'} />
          </Field>
          <Field label="HEADER" hint="blank = Authorization: Bearer">
            <Input value={draft.header} onChange={(e) => set({ header: e.target.value })} placeholder="X-API-Key" />
          </Field>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="USERNAME" required>
            <Input autoComplete="off" value={draft.username} onChange={(e) => set({ username: e.target.value })} />
          </Field>
          <Field label="PASSWORD" hint={editing ? 'leave blank to keep' : 'never shown again'} required={!editing}>
            <Input type="password" autoComplete="new-password" value={draft.password}
              onChange={(e) => set({ password: e.target.value })} placeholder={editing ? 'saved' : ''} />
          </Field>
        </div>
      )}
      <Field label="DESCRIPTION" hint="tells agents when to use it">
        <Input value={draft.description} onChange={(e) => set({ description: e.target.value })} placeholder="GitHub API, read and write issues" />
      </Field>
      <div className="flex gap-2">
        <button type="submit" disabled={busy}
          className="h-9 rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60">
          {busy ? 'Saving…' : editing ? 'Save' : 'Add'}
        </button>
        <button type="button" onClick={onCancel} className="h-9 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
          Cancel
        </button>
      </div>
    </form>
  );
}
