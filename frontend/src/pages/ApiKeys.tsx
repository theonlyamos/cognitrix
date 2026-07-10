import { useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { PageHeader, LoadingState, ErrorState, EmptyState } from '@/components/list-ui';
import { Field, CheckList } from '@/components/form';
import { cn } from '@/lib/utils';

interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  allowed_agents: string[];
  allowed_teams: string[];
  rate_limit: number | null;
  expires_at: string | null;
  last_used_at: string | null;
  revoked: boolean;
  created_at?: string;
}

interface CreatedKey extends ApiKey {
  key: string;
  webhook_secret: string;
}

interface Agent { id: string; name: string; llm?: { provider?: string; model?: string } }
interface Team { id: string; name: string }

const SCOPES = [
  { value: 'chat', label: 'chat', sub: 'call agents (generate, OpenAI shim)' },
  { value: 'run', label: 'run', sub: 'start / cancel tasks and teams' },
  { value: 'read', label: 'read', sub: 'GET any resource' },
  { value: 'write', label: 'write', sub: 'create / edit / delete (full CRUD)' },
];

const SCOPE_BADGE: Record<string, string> = {
  chat: 'text-accent-ink border-accent/40',
  run: 'text-ok border-ok/40',
  read: 'text-fg-dim border-line',
  write: 'text-danger-ink border-danger/40',
};

// "now" as a datetime-local string (local wall-clock, matching the input) so
// the expiry picker can't select a past instant — an already-expired key.
function localNowMin(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function CopyRow({ id, label, value }: { id: string; label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — the value is selectable in the field */
    }
  };
  return (
    <div>
      <label htmlFor={id} className="mb-1 block font-mono text-[11px] tracking-[0.12em] text-fg-dim">{label}</label>
      <div className="flex gap-2">
        <input
          id={id}
          readOnly
          value={value}
          onFocus={(e) => e.currentTarget.select()}
          className="h-10 w-full rounded border border-line bg-bg px-3 font-mono text-[12px] text-fg"
        />
        <Button type="button" variant="outline" size="sm" onClick={copy} className="flex-none">
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
    </div>
  );
}

export default function ApiKeys() {
  const { data, loading, error, refetch } = useResource<ApiKey[]>('/api-keys');
  const { data: agentList } = useResource<Agent[]>('/agents');
  const { data: teamList } = useResource<Team[]>('/teams');
  const keys = data ?? [];

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<Set<string>>(new Set(['chat']));
  const [agents, setAgents] = useState<Set<string>>(new Set());
  const [teams, setTeams] = useState<Set<string>>(new Set());
  const [expiresAt, setExpiresAt] = useState('');
  const [rateLimit, setRateLimit] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedKey | null>(null);

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, v: string) => {
    const next = new Set(set);
    next.has(v) ? next.delete(v) : next.add(v);
    setter(next);
  };

  const resetForm = () => {
    setName('');
    setScopes(new Set(['chat']));
    setAgents(new Set());
    setTeams(new Set());
    setExpiresAt('');
    setRateLimit('');
    setFormError(null);
  };

  const create = async () => {
    if (!name.trim()) return setFormError('Name is required.');
    if (scopes.size === 0) return setFormError('Select at least one scope.');
    setSaving(true);
    setFormError(null);
    try {
      const payload: Record<string, unknown> = {
        name: name.trim(),
        scopes: [...scopes],
        allowed_agents: [...agents],
        allowed_teams: [...teams],
      };
      // datetime-local is naive local wall-clock; send it as a UTC ISO string
      // with offset so the server stores the instant the user actually meant
      // (a bare "YYYY-MM-DDTHH:mm" would be read back as UTC — off by the tz).
      if (expiresAt) payload.expires_at = new Date(expiresAt).toISOString();
      if (rateLimit) payload.rate_limit = Number(rateLimit);
      const res = await api.post<CreatedKey>('/api-keys', payload);
      setCreated(res.data);
      setShowForm(false);
      resetForm();
      refetch({ silent: true });
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const revoke = async (key: ApiKey) => {
    if (!window.confirm(`Revoke "${key.name}"? Any client using it stops working immediately.`)) return;
    try {
      await api.delete(`/api-keys/${key.id}`);
      refetch({ silent: true });
    } catch (err) {
      window.alert(errorMessage(err));
    }
  };

  return (
    <div className="flex-1 flex flex-col h-screen min-w-0 overflow-hidden bg-bg text-fg">
      <PageHeader title="API Keys" subtitle={`programmatic access · ${keys.length}`}>
        <Button size="sm" onClick={() => { setShowForm((s) => !s); setCreated(null); }}>
          {showForm ? 'Close' : '+ New key'}
        </Button>
      </PageHeader>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-6 px-6 py-8">
          {/* One-time secret card */}
          {created && (
            <div className="space-y-4 rounded border border-accent/50 bg-accent/5 p-5">
              <div>
                <h2 className="text-[15px] font-semibold">Key created — copy it now</h2>
                <p className="mt-1 text-[13px] text-fg-dim">
                  This is the only time the secret and webhook signing key are shown. Store them somewhere safe.
                </p>
              </div>
              <CopyRow id="created-api-key" label="API KEY" value={created.key} />
              <CopyRow id="created-webhook-secret" label="WEBHOOK SECRET" value={created.webhook_secret} />
              <div className="flex justify-end">
                <Button variant="outline" size="sm" onClick={() => setCreated(null)}>Done</Button>
              </div>
            </div>
          )}

          {/* Create form */}
          {showForm && (
            <div className="space-y-5 rounded border border-line bg-panel p-5">
              {formError && (
                <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{formError}</p>
              )}
              <Field label="NAME" required>
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. ci-pipeline" />
              </Field>
              <Field label={`SCOPES · ${scopes.size}`} required hint="what this key may do" composite>
                <CheckList
                  options={SCOPES}
                  selected={scopes}
                  onToggle={(v) => toggle(scopes, setScopes, v)}
                />
              </Field>
              <Field label={`AGENT ALLOWLIST · ${agents.size}`} hint="empty = all agents" composite>
                <CheckList
                  options={(agentList || []).map((a) => ({ value: a.id, label: a.name, sub: `${a.llm?.provider || '—'} · ${a.llm?.model || '—'}` }))}
                  selected={agents}
                  onToggle={(v) => toggle(agents, setAgents, v)}
                  empty="no agents"
                />
              </Field>
              <Field label={`TEAM ALLOWLIST · ${teams.size}`} hint="empty = all teams" composite>
                <CheckList
                  options={(teamList || []).map((t) => ({ value: t.id, label: t.name }))}
                  selected={teams}
                  onToggle={(v) => toggle(teams, setTeams, v)}
                  empty="no teams"
                />
              </Field>
              <div className="grid grid-cols-2 gap-4">
                <Field label="EXPIRES" hint="optional">
                  <Input type="datetime-local" min={localNowMin()} value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} />
                </Field>
                <Field label="RATE LIMIT" hint="req/min, optional">
                  <Input type="number" min={1} value={rateLimit} onChange={(e) => setRateLimit(e.target.value)} placeholder="default" />
                </Field>
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => { setShowForm(false); resetForm(); }}>Cancel</Button>
                <Button size="sm" onClick={create} disabled={saving}>{saving ? <span role="status">Creating…</span> : 'Create key'}</Button>
              </div>
            </div>
          )}

          {/* List */}
          {loading ? (
            <LoadingState label="loading keys…" />
          ) : error ? (
            <ErrorState message={error} onRetry={refetch} />
          ) : keys.length === 0 && !showForm ? (
            <EmptyState
              icon={<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="8" cy="15" r="4" /><path d="M10.85 12.15 19 4M18 5l2 2M15 8l2 2" /></svg>}
              title="No API keys"
              desc="Create a key to call agents, run teams, or use the OpenAI-compatible endpoint from your own apps."
              action={<Button size="sm" onClick={() => setShowForm(true)}>+ New key</Button>}
            />
          ) : (
            <ul className="rounded border border-line">
              {keys.map((k) => (
                <li key={k.id} className={cn('flex items-center gap-3 border-b border-line px-4 py-3 last:border-b-0', k.revoked && 'opacity-55')}>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[14px] font-medium">{k.name}</span>
                      <span className="font-mono text-[11px] text-fg-dim">{k.prefix}…</span>
                      {k.revoked && <span className="rounded border border-danger/40 px-1.5 py-px font-mono text-[10px] text-danger-ink">REVOKED</span>}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      {k.scopes.map((s) => (
                        <span key={s} className={cn('rounded border px-1.5 py-px font-mono text-[10px]', SCOPE_BADGE[s] || 'border-line text-fg-dim')}>{s}</span>
                      ))}
                      <span className="font-mono text-[10.5px] text-fg-dim">
                        {k.allowed_agents.length ? `${k.allowed_agents.length} agent(s)` : 'all agents'}
                        {' · '}
                        {k.allowed_teams.length ? `${k.allowed_teams.length} team(s)` : 'all teams'}
                        {k.rate_limit ? ` · ${k.rate_limit}/min` : ''}
                        {k.expires_at ? ` · expires ${k.expires_at.slice(0, 16)}` : ''}
                        {` · ${k.last_used_at ? `used ${k.last_used_at.slice(0, 16)}` : 'never used'}`}
                      </span>
                    </div>
                  </div>
                  {!k.revoked && (
                    <Button variant="ghost" size="sm" onClick={() => revoke(k)} className="flex-none hover:text-danger-ink">Revoke</Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
