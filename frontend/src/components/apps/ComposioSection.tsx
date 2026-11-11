import { useState, type FormEvent } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { Field } from '@/components/form';
import {
  disconnectComposioApp,
  removeComposioKey,
  saveComposioKey,
  searchComposioApps,
  signInWithBrowser,
  startComposioSignIn,
  testComposioApp,
  type CheckResult,
  type ComposioState,
  type ComposioToolkit,
} from '@/lib/apps';
import { Group, StatusPill, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { CheckList } from './CheckList';

/** More apps through the person's own Composio account. Off until they add a key. */
export function ComposioSection({ state, onChanged }: { state: ComposioState; onChanged: () => void | Promise<void> }) {
  const [adding, setAdding] = useState(false);
  const [key, setKey] = useState('');
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<ComposioToolkit[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, CheckResult>>({});

  const run = async (name: string, action: () => Promise<{ status: string; error: string | null } | void>, reload = true) => {
    setBusy(name);
    setErrors((cur) => {
      const next = { ...cur };
      delete next[name];
      return next;
    });
    try {
      const result = await action();
      if (result && result.status === 'error') setErrors((cur) => ({ ...cur, [name]: result.error || 'Sign-in failed.' }));
    } catch (e) {
      setErrors((cur) => ({ ...cur, [name]: errorMessage(e, 'Something went wrong. Please try again.') }));
    } finally {
      setBusy(null);
      if (reload) await onChanged();
    }
  };

  const saveKey = (e: FormEvent) => {
    e.preventDefault();
    void run('key', async () => {
      await saveComposioKey(key.trim());
      setKey('');
      setAdding(false);
    });
  };

  const removeKey = () =>
    run('key', async () => {
      await removeComposioKey();
      setFound(null);
      setQuery('');
    });

  const search = (e?: FormEvent) => {
    e?.preventDefault();
    void run('search', async () => {
      const page = await searchComposioApps(query.trim());
      setFound(page.toolkits);
      setNextCursor(page.next_cursor);
    }, false);
  };

  const more = () =>
    run('search', async () => {
      const page = await searchComposioApps(query.trim(), nextCursor);
      setFound((cur) => [...(cur || []), ...page.toolkits]);
      setNextCursor(page.next_cursor);
    }, false);

  const connect = (slug: string, name: string) =>
    run(`app:${slug}`, () => signInWithBrowser(() => startComposioSignIn(slug, name)));

  const connected = new Map(state.connections.map((c) => [c.slug, c]));

  return (
    <section aria-label="More apps with Composio">
      <SubHeading>MORE APPS WITH COMPOSIO · OPTIONAL</SubHeading>
      <p className="-mt-1 mb-2 max-w-[70ch] text-[12px] leading-relaxed text-fg-dim">
        Composio is a separate service with ready-made sign-ins for hundreds of apps. Add your own Composio API key to use it.
        Sign-ins made here are kept by Composio, and what your agents read or send through these apps passes through its
        servers. Leave this off to keep everything between this server and each app.
      </p>

      {!state.has_key && !adding && (
        <button type="button" onClick={() => setAdding(true)} className={buttonClass}>Add a Composio API key</button>
      )}
      {!state.has_key && adding && (
        <form onSubmit={saveKey} aria-label="Composio API key" className="flex flex-wrap items-end gap-2 rounded-[12px] border border-line bg-panel p-3.5">
          <div className="min-w-[220px] flex-1">
            <Field label="COMPOSIO API KEY" hint="From your Composio dashboard, under Settings › API keys.">
              <Input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
            </Field>
          </div>
          <button type="submit" disabled={!key.trim() || busy === 'key'} className={primaryButtonClass}>
            {busy === 'key' ? 'Checking…' : 'Save key'}
          </button>
          <button type="button" onClick={() => setAdding(false)} className={quietButtonClass}>Cancel</button>
        </form>
      )}
      {errors.key && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{errors.key}</p>}

      {state.has_key && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
            <StatusPill tone="ok">key saved</StatusPill>
            <span className="font-mono text-fg-dim">{state.hint}</span>
            <span className="flex-1" />
            <button type="button" disabled={busy === 'key'} onClick={() => void removeKey()} className={quietButtonClass}>
              Remove key
            </button>
          </div>

          {state.connections.length > 0 && (
            <Group>
              {state.connections.map((c) => {
                const name = `app:${c.slug}`;
                const stale = c.status !== 'connected';
                return (
                  <div key={c.server_id} className="px-3.5 py-3">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                      <div className="min-w-0 flex-1 basis-[180px]">
                        <div className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium">
                          {c.name}
                          {stale ? <StatusPill tone="warn">needs sign-in</StatusPill> : <StatusPill tone="ok">connected</StatusPill>}
                        </div>
                        <div className="mt-0.5 text-[12px] leading-snug text-fg-dim">
                          {c.account ? <>Signed in as <span className="font-mono">{c.account}</span> · </> : null}through Composio
                        </div>
                      </div>
                      <div className="flex flex-none flex-wrap items-center gap-2">
                        {stale ? (
                          <button type="button" disabled={busy === name} onClick={() => void connect(c.slug, c.name)} className={primaryButtonClass}>
                            {busy === name ? 'Waiting for sign-in…' : 'Sign in'}
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={busy === name}
                            aria-label={`Test ${c.name}`}
                            onClick={() => void run(name, async () => {
                              const result = await testComposioApp(c.server_id);
                              setResults((cur) => ({ ...cur, [c.server_id]: result }));
                            }, false)}
                            className={quietButtonClass}
                          >
                            {busy === name ? 'Testing…' : 'Test'}
                          </button>
                        )}
                        <button type="button" disabled={busy === name} aria-label={`Disconnect ${c.name}`} onClick={() => void run(name, () => disconnectComposioApp(c.server_id))} className={quietButtonClass}>
                          Disconnect
                        </button>
                      </div>
                    </div>
                    {results[c.server_id] && <CheckList result={results[c.server_id]} label={`${c.name} test`} />}
                    {errors[name] && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{errors[name]}</p>}
                  </div>
                );
              })}
            </Group>
          )}

          <form onSubmit={search} role="search" aria-label="Find an app on Composio" className="flex flex-wrap gap-2">
            <label htmlFor="composio-search" className="sr-only">Find an app</label>
            <Input id="composio-search" className="min-w-[200px] flex-1" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find an app, e.g. HubSpot" />
            <button type="submit" disabled={busy === 'search'} className={buttonClass}>
              {busy === 'search' ? 'Searching…' : found === null && !query.trim() ? 'Browse apps' : 'Search'}
            </button>
          </form>
          {errors.search && <p role="alert" className="text-[12px] text-danger-ink">{errors.search}</p>}

          {found !== null && found.length === 0 && <p className="text-[12.5px] text-fg-dim">No apps match that.</p>}
          {found !== null && found.length > 0 && (
            <Group>
              {found.map((t) => {
                const name = `app:${t.slug}`;
                const have = connected.get(t.slug);
                return (
                  <div key={t.slug} className="px-3.5 py-3">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                      <div className="min-w-0 flex-1 basis-[180px]">
                        <div className="text-[13.5px] font-medium">{t.name}</div>
                        <div className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-fg-dim">
                          {t.description || t.categories.join(', ')}
                          {t.tools_count ? ` · ${t.tools_count} tools` : ''}
                        </div>
                      </div>
                      {have && have.status === 'connected' ? (
                        <StatusPill tone="ok">connected</StatusPill>
                      ) : t.connectable ? (
                        <button type="button" disabled={busy === name} aria-label={`Connect ${t.name}`} onClick={() => void connect(t.slug, t.name)} className={buttonClass}>
                          {busy === name ? 'Waiting for sign-in…' : 'Connect'}
                        </button>
                      ) : (
                        <span className="max-w-[220px] text-[12px] text-fg-dim">Needs its own sign-in set up in your Composio dashboard</span>
                      )}
                    </div>
                    {errors[name] && !have && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{errors[name]}</p>}
                  </div>
                );
              })}
            </Group>
          )}
          {found !== null && nextCursor && (
            <button type="button" disabled={busy === 'search'} onClick={() => void more()} className={quietButtonClass}>Show more</button>
          )}
        </div>
      )}
    </section>
  );
}
