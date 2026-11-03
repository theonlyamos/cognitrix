import { useEffect, useState, type FormEvent } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { Field } from '@/components/form';
import { cn } from '@/lib/utils';
import {
  addCustomApp,
  connectWithToken,
  disconnectApp,
  listApps,
  signInWithBrowser,
  signOutServer,
  startAppSignIn,
  startServerSignIn,
  type AppConnection,
  type AppList,
  type CatalogApp,
} from '@/lib/apps';
import { Group, StatusPill, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';

const TILE: Record<string, string> = {
  github: 'bg-[#24292f] text-white',
  gmail: 'bg-[#ea4335]/15 text-[#c5221f] dark:text-[#f28b82]',
  'google-calendar': 'bg-[#1a73e8]/15 text-[#1a73e8] dark:text-[#8ab4f8]',
  'google-drive': 'bg-[#188038]/15 text-[#188038] dark:text-[#81c995]',
  'google-docs': 'bg-[#4285f4]/15 text-[#1967d2] dark:text-[#8ab4f8]',
  'google-sheets': 'bg-[#0f9d58]/15 text-[#0b8043] dark:text-[#81c995]',
  'google-slides': 'bg-[#f4b400]/20 text-[#b06000] dark:text-[#fdd663]',
};

function AppTile({ id, name }: { id: string; name: string }) {
  const letters = name.replace(/^Google /, '').slice(0, 2);
  return (
    <span aria-hidden="true" className={cn(
      'grid h-9 w-9 flex-none place-items-center rounded-[9px] border border-line text-[13px] font-semibold',
      TILE[id] || 'bg-panel-2 text-accent-ink',
    )}>
      {letters}
    </span>
  );
}

function Status({ connection }: { connection: AppConnection | null }) {
  if (!connection || connection.status === 'signed_out') return null;
  if (connection.status === 'needs_reauth') return <StatusPill tone="warn">needs reconnect</StatusPill>;
  return <StatusPill tone="ok">connected</StatusPill>;
}

/** Settings › Connected apps: sign in to GitHub, Google apps and other hosted
 *  MCP servers, so agents can use them. Each connection is yours alone. */
export function ConnectedAppsCard() {
  const [list, setList] = useState<AppList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tokenFor, setTokenFor] = useState<string | null>(null);
  const [token, setToken] = useState('');
  const [adding, setAdding] = useState(false);
  const [custom, setCustom] = useState({ name: '', url: '' });

  const load = () =>
    listApps()
      .then((l) => { setList(l); setLoadError(null); })
      .catch((e) => setLoadError(errorMessage(e, 'Could not load connected apps.')));

  useEffect(() => { void load(); }, []);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!list) return <p className="text-[13px] text-fg-dim">Loading…</p>;

  const setError = (key: string, message: string | null) =>
    setErrors((cur) => {
      const next = { ...cur };
      if (message) next[key] = message;
      else delete next[key];
      return next;
    });

  const run = async (key: string, action: () => Promise<{ status: string; error: string | null } | void>) => {
    setBusy(key);
    setError(key, null);
    try {
      const result = await action();
      if (result && result.status === 'error') setError(key, result.error || 'Sign-in failed.');
    } catch (e) {
      setError(key, errorMessage(e, 'Something went wrong. Please try again.'));
    } finally {
      setBusy(null);
      await load();
    }
  };

  const connect = (app: CatalogApp) => run(app.id, () => signInWithBrowser(() => startAppSignIn(app.id)));
  const reconnectCustom = (c: AppConnection) => run(c.server_id, () => signInWithBrowser(() => startServerSignIn(c.server_id)));

  const saveToken = (app: CatalogApp) => (e: FormEvent) => {
    e.preventDefault();
    void run(app.id, async () => {
      await connectWithToken(app.id, token);
      setToken('');
      setTokenFor(null);
    });
  };

  const addByUrl = (e: FormEvent) => {
    e.preventDefault();
    const key = 'custom';
    void run(key, async () => {
      let pendingError: string | undefined;
      const result = await signInWithBrowser(async () => {
        const added = await addCustomApp(custom.name.trim(), custom.url.trim());
        pendingError = added.error;
        return added.flow;
      });
      setAdding(false);
      setCustom({ name: '', url: '' });
      if (pendingError) return { status: 'error', error: `Added, but sign-in couldn't start: ${pendingError}` };
      return result;
    });
  };

  const groups = Array.from(new Set(list.apps.map((a) => a.category || 'Other')));

  return (
    <div className="space-y-6">
      {groups.map((group) => (
        <section key={group}>
          <SubHeading>{group.toUpperCase()}</SubHeading>
          {list.is_admin && list.apps.some((a) => (a.category || 'Other') === group && a.provider === 'google' && a.available) && (
            <p className="-mt-1 mb-2 text-[12px] leading-relaxed text-fg-dim">
              Google's app servers are in preview. Turn on each app's MCP API (for example "Gmail MCP API") in the Google Cloud project that owns <span className="font-mono">GOOGLE_CLIENT_ID</span>.
            </p>
          )}
          <Group>
            {list.apps.filter((a) => (a.category || 'Other') === group).map((app) => {
              const conn = app.connection;
              const on = conn && conn.status !== 'signed_out';
              const stale = conn?.status === 'needs_reauth';
              const working = busy === app.id;
              return (
                <div key={app.id} className="px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <AppTile id={app.id} name={app.name} />
                    <div className="min-w-0 flex-1 basis-[180px]">
                      <div className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium">
                        {app.name} <Status connection={conn} />
                      </div>
                      <div className="mt-0.5 text-[12px] leading-snug text-fg-dim">
                        {on && conn.account ? <>Signed in as <span className="font-mono">{conn.account}</span></> : app.description}
                      </div>
                    </div>
                    <div className="flex flex-none flex-wrap items-center gap-2">
                      {on && !stale ? (
                        <button type="button" disabled={working} onClick={() => void run(app.id, () => disconnectApp(app.id))} className={quietButtonClass}>
                          Disconnect
                        </button>
                      ) : (
                        <>
                          {app.token_help && (
                            <button type="button" onClick={() => { setTokenFor(tokenFor === app.id ? null : app.id); setToken(''); }} className={quietButtonClass}>
                              Use a token
                            </button>
                          )}
                          {app.available ? (
                            <button type="button" disabled={working} onClick={() => void connect(app)} className={stale ? primaryButtonClass : buttonClass}>
                              {working ? 'Waiting for sign-in…' : stale ? 'Reconnect' : 'Connect'}
                            </button>
                          ) : (
                            !app.token_help && <span className="text-[12px] text-fg-dim">Not set up on this server</span>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                  {!app.available && list.is_admin && (
                    <p className="mt-2 text-[12px] leading-relaxed text-fg-dim">
                      {app.setup}
                      {app.provider && list.callback_urls[app.provider] && (
                        <> Its redirect URI is <span className="break-all font-mono">{list.callback_urls[app.provider]}</span>.</>
                      )}
                    </p>
                  )}
                  {tokenFor === app.id && (
                    <form onSubmit={saveToken(app)} className="mt-3 flex flex-wrap items-end gap-2" aria-label={`${app.name} token`}>
                      <div className="min-w-[220px] flex-1">
                        <Field label="ACCESS TOKEN" hint={app.token_help || undefined}>
                          <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="ghp_…" />
                        </Field>
                      </div>
                      <button type="submit" disabled={!token.trim() || working} className={buttonClass}>Save token</button>
                    </form>
                  )}
                  {errors[app.id] && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{errors[app.id]}</p>}
                </div>
              );
            })}
          </Group>
        </section>
      ))}

      <section>
        <SubHeading>ADDED BY ADDRESS</SubHeading>
        {list.custom.length > 0 && (
          <Group className="mb-3">
            {list.custom.map((c) => {
              const stale = c.status === 'needs_reauth';
              const signedOut = c.status === 'signed_out';
              return (
                <div key={c.server_id} className="px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <AppTile id="" name={c.name} />
                    <div className="min-w-0 flex-1 basis-[180px]">
                      <div className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium">
                        <span className="font-mono">{c.name}</span> <Status connection={c} />
                      </div>
                      <div className="mt-0.5 break-all font-mono text-[11.5px] text-fg-dim">{c.url}</div>
                    </div>
                    <div className="flex flex-none items-center gap-2">
                      {!signedOut && !stale && (
                        <button type="button" onClick={() => void run(c.server_id, () => signOutServer(c.server_id))} className={quietButtonClass}>
                          Sign out
                        </button>
                      )}
                      {(signedOut || stale) && (
                        <button type="button" disabled={busy === c.server_id} onClick={() => void reconnectCustom(c)} className={stale ? primaryButtonClass : buttonClass}>
                          {busy === c.server_id ? 'Waiting for sign-in…' : stale ? 'Reconnect' : 'Sign in'}
                        </button>
                      )}
                    </div>
                  </div>
                  {errors[c.server_id] && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{errors[c.server_id]}</p>}
                </div>
              );
            })}
          </Group>
        )}
        {adding ? (
          <form onSubmit={addByUrl} aria-label="Add an app by address" className="space-y-3 rounded-[12px] border border-line bg-panel p-3.5">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-[180px_1fr]">
              <Field label="NAME" hint="letters, numbers, - and _" required>
                <Input value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} placeholder="tracker" />
              </Field>
              <Field label="MCP SERVER ADDRESS" required>
                <Input value={custom.url} onChange={(e) => setCustom({ ...custom, url: e.target.value })} placeholder="https://mcp.example.com/mcp" />
              </Field>
            </div>
            <p className="text-[12px] text-fg-dim">If the server asks you to sign in, a sign-in window opens next.</p>
            <div className="flex gap-2">
              <button type="submit" disabled={busy === 'custom' || !custom.name.trim() || !custom.url.trim()} className={primaryButtonClass}>
                {busy === 'custom' ? 'Waiting for sign-in…' : 'Add'}
              </button>
              <button type="button" onClick={() => setAdding(false)} className={quietButtonClass}>Cancel</button>
            </div>
          </form>
        ) : (
          <button type="button" onClick={() => setAdding(true)} className={buttonClass}>+ Add an app by address</button>
        )}
        {errors.custom && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{errors.custom}</p>}
      </section>
    </div>
  );
}
