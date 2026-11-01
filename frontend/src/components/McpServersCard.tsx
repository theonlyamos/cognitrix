import { useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Field } from '@/components/form';
import { cn } from '@/lib/utils';
import {
  KEEP_SECRET,
  addMcpServer,
  deleteMcpServer,
  listMcpServers,
  testMcpServer,
  updateMcpServer,
  type McpServer,
  type McpServerList,
  type McpTestResult,
  type McpTransport,
} from '@/lib/mcp';

interface Pair { key: string; value: string }

interface Draft {
  id: string | null;
  name: string;
  description: string;
  transport: McpTransport;
  url: string;
  command: string;
  args: string;
  headers: Pair[];
  env: Pair[];
  shared: boolean;
}

const EMPTY: Draft = {
  id: null, name: '', description: '', transport: 'http', url: '', command: '', args: '',
  headers: [{ key: '', value: '' }], env: [{ key: '', value: '' }], shared: false,
};

function draftFrom(server: McpServer): Draft {
  // Stored values never come back, so existing names start as "keep".
  const keep = (names: string[]) =>
    names.length ? names.map((key) => ({ key, value: KEEP_SECRET })) : [{ key: '', value: '' }];
  return {
    id: server.id,
    name: server.name,
    description: server.description,
    transport: server.transport,
    url: server.url ?? '',
    command: server.command ?? '',
    args: server.args.join(' '),
    headers: keep(server.header_names),
    env: keep(server.env_names),
    shared: server.shared,
  };
}

function toRecord(pairs: Pair[]): Record<string, string> {
  return Object.fromEntries(pairs.filter((p) => p.key.trim() && p.value !== "").map((p) => [p.key.trim(), p.value]));
}

/** Settings › MCP servers: servers agents can use, added per person.
 *  Shared ones (added by an admin, or from the CLI's mcp.json) are listed too. */
export function McpServersCard() {
  const [list, setList] = useState<McpServerList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, McpTestResult | 'running'>>({});

  const load = () =>
    listMcpServers()
      .then(setList)
      .catch((e) => setLoadError(errorMessage(e, 'Could not load MCP servers.')));

  useEffect(() => { void load(); }, []);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!list) return <p className="text-[13px] text-fg-dim">Loading…</p>;

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    const stdio = draft.transport === 'stdio';
    const input = {
      name: draft.name.trim(),
      transport: draft.transport,
      description: draft.description,
      url: stdio ? null : draft.url.trim(),
      headers: stdio ? {} : toRecord(draft.headers),
      command: stdio ? draft.command.trim() : null,
      args: stdio ? draft.args.split(/\s+/).filter(Boolean) : [],
      env: stdio ? toRecord(draft.env) : {},
    };
    try {
      if (draft.id) await updateMcpServer(draft.id, input);
      else await addMcpServer({ ...input, shared: draft.shared });
      setDraft(null);
      await load();
    } catch (e) {
      setError(errorMessage(e, 'Could not save the server.'));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (server: McpServer) => {
    if (!server.id) return;
    setBusy(true);
    setError(null);
    try {
      await deleteMcpServer(server.id);
      await load();
    } catch (e) {
      setError(errorMessage(e, 'Could not remove the server.'));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (server: McpServer) => {
    if (!server.id) return;
    try {
      await updateMcpServer(server.id, { enabled: !server.enabled });
      await load();
    } catch (e) {
      setError(errorMessage(e, 'Could not change the server.'));
    }
  };

  const test = async (server: McpServer) => {
    setTests((t) => ({ ...t, [server.ref]: 'running' }));
    try {
      const result = await testMcpServer(server.ref);
      setTests((t) => ({ ...t, [server.ref]: result }));
      if (result.connected) void load();
    } catch (e) {
      setTests((t) => ({ ...t, [server.ref]: { connected: false, tools: [], error: errorMessage(e) } }));
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[13px] leading-relaxed text-fg-dim">
        MCP servers give agents extra tools, such as a notes app or an issue tracker. Pick which ones an agent can use on its edit page.
        Tools that delete data, send something outside the app, or do something Cognitrix can't recognise wait for your approval before they run.
      </p>

      {list.servers.length === 0 ? (
        <p className="rounded-[10px] border border-line bg-bg px-3 py-3 text-[12.5px] text-fg-dim">No servers yet.</p>
      ) : (
        <ul className="divide-y divide-line rounded-[10px] border border-line bg-bg">
          {list.servers.map((server) => {
            const result = tests[server.ref];
            return (
              <li key={server.ref} className="px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    aria-hidden="true"
                    className={cn('h-2 w-2 flex-none rounded-full', server.connected ? 'bg-ok' : 'bg-line')}
                  />
                  <span className="font-mono text-[13px]">{server.name}</span>
                  <span className="rounded border border-line px-1.5 font-mono text-[10.5px] text-fg-dim">{server.transport}</span>
                  {server.shared && (
                    <span className="rounded border border-line px-1.5 text-[11px] text-fg-dim">
                      {server.source === 'config_file' ? 'shared · mcp.json' : 'shared'}
                    </span>
                  )}
                  {!server.enabled && <span className="text-[11.5px] text-fg-dim">off</span>}
                  <span className="flex-1" />
                  <button type="button" onClick={() => void test(server)} disabled={result === 'running'}
                    className="text-[12.5px] text-accent-ink hover:underline disabled:opacity-60">
                    {result === 'running' ? 'Connecting…' : 'Test'}
                  </button>
                  {server.can_edit && (
                    <>
                      <button type="button" onClick={() => void toggle(server)} className="text-[12.5px] text-fg-dim hover:text-fg">
                        {server.enabled ? 'Turn off' : 'Turn on'}
                      </button>
                      <button type="button" onClick={() => { setError(null); setDraft(draftFrom(server)); }}
                        className="text-[12.5px] text-fg-dim hover:text-fg">
                        Edit
                      </button>
                      <button type="button" onClick={() => void remove(server)} disabled={busy}
                        className="text-[12.5px] text-danger-ink hover:underline disabled:opacity-60">
                        Remove
                      </button>
                    </>
                  )}
                </div>
                {server.description && <p className="mt-1 text-[12px] text-fg-dim">{server.description}</p>}
                {result && result !== 'running' && (
                  result.connected ? (
                    <div className="mt-2 text-[12px]">
                      <p className="text-fg-dim">{result.tools.length} tool{result.tools.length === 1 ? '' : 's'}</p>
                      <ul className="mt-1 space-y-0.5">
                        {result.tools.map((t) => (
                          <li key={t.name} className="flex gap-2">
                            <span className="font-mono">{t.name}</span>
                            <span className="text-fg-dim">{(t.asks_first ?? !t.read_only) ? 'asks first' : t.read_only ? 'reads only' : 'runs without asking'}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : (
                    <p role="alert" className="mt-2 text-[12px] text-danger-ink">{result.error}</p>
                  )
                )}
              </li>
            );
          })}
        </ul>
      )}

      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}

      {draft ? (
        <ServerForm
          draft={draft}
          setDraft={setDraft}
          canAddLocal={list.can_add_local}
          canAddShared={list.can_add_shared}
          busy={busy}
          onSave={() => void save()}
          onCancel={() => { setDraft(null); setError(null); }}
        />
      ) : (
        <button type="button" onClick={() => { setError(null); setDraft({ ...EMPTY }); }}
          className="h-9 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
          Add server
        </button>
      )}
    </div>
  );
}

function PairRows({ label, pairs, onChange, valuePlaceholder }: {
  label: string; pairs: Pair[]; onChange: (pairs: Pair[]) => void; valuePlaceholder: string;
}) {
  const set = (i: number, patch: Partial<Pair>) => onChange(pairs.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  return (
    <Field label={label} hint="values are never shown again" composite>
      <div role="group" className="space-y-2">
        {pairs.map((p, i) => (
          <div key={i} className="flex gap-2">
            <Input aria-label={`${label} name ${i + 1}`} value={p.key} placeholder="Name" onChange={(e) => set(i, { key: e.target.value })} />
            <Input
              aria-label={`${label} value ${i + 1}`}
              type="password"
              autoComplete="off"
              value={p.value === KEEP_SECRET ? '' : p.value}
              placeholder={p.value === KEEP_SECRET ? 'saved (leave blank to keep)' : valuePlaceholder}
              onChange={(e) => set(i, { value: e.target.value === '' && p.value === KEEP_SECRET ? KEEP_SECRET : e.target.value })}
            />
            <button type="button" aria-label={`Remove ${label.toLowerCase()} ${i + 1}`}
              onClick={() => onChange(pairs.length > 1 ? pairs.filter((_, j) => j !== i) : [{ key: '', value: '' }])}
              className="px-2 text-[13px] text-fg-dim hover:text-fg">
              ×
            </button>
          </div>
        ))}
        <button type="button" onClick={() => onChange([...pairs, { key: '', value: '' }])} className="text-[12.5px] text-accent-ink hover:underline">
          Add another
        </button>
      </div>
    </Field>
  );
}

function ServerForm({ draft, setDraft, canAddLocal, canAddShared, busy, onSave, onCancel }: {
  draft: Draft;
  setDraft: (d: Draft) => void;
  canAddLocal: boolean;
  canAddShared: boolean;
  busy: boolean;
  onSave: () => void;
  onCancel: () => void;
}) {
  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });
  const stdio = draft.transport === 'stdio';
  return (
    <form
      aria-label={draft.id ? `Edit ${draft.name}` : 'Add MCP server'}
      className="space-y-3 rounded-[10px] border border-line bg-bg p-3"
      onSubmit={(e) => { e.preventDefault(); onSave(); }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="NAME" hint="letters, numbers, - and _" required>
          <Input value={draft.name} onChange={(e) => set({ name: e.target.value })} placeholder="notion" />
        </Field>
        <Field label="TYPE">
          <Select value={draft.transport} onChange={(e) => set({ transport: e.target.value as McpTransport })}>
            <option value="http">Remote (HTTP)</option>
            <option value="sse">Remote (SSE, older servers)</option>
            {(canAddLocal || stdio) && <option value="stdio">Local command (stdio)</option>}
          </Select>
        </Field>
      </div>
      <Field label="DESCRIPTION">
        <Input value={draft.description} onChange={(e) => set({ description: e.target.value })} placeholder="What it's for" />
      </Field>
      {stdio ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="COMMAND" required>
              <Input value={draft.command} onChange={(e) => set({ command: e.target.value })} placeholder="npx" />
            </Field>
            <Field label="ARGUMENTS" hint="separated by spaces">
              <Input value={draft.args} onChange={(e) => set({ args: e.target.value })} placeholder="-y @modelcontextprotocol/server-memory" />
            </Field>
          </div>
          <PairRows label="ENVIRONMENT" pairs={draft.env} onChange={(env) => set({ env })} valuePlaceholder="value" />
        </>
      ) : (
        <>
          <Field label="ADDRESS" required>
            <Input value={draft.url} onChange={(e) => set({ url: e.target.value })} placeholder="https://mcp.example.com/mcp" />
          </Field>
          <PairRows label="HEADERS" pairs={draft.headers} onChange={(headers) => set({ headers })} valuePlaceholder="Bearer …" />
        </>
      )}
      {canAddShared && !draft.id && (
        <label className="flex items-center gap-2 text-[13px]">
          <input type="checkbox" checked={draft.shared} onChange={(e) => set({ shared: e.target.checked })} className="accent-[var(--accent)]" />
          Share with everyone on this server
        </label>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={busy}
          className="h-9 rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60">
          {busy ? 'Saving…' : draft.id ? 'Save' : 'Add'}
        </button>
        <button type="button" onClick={onCancel} className="h-9 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
          Cancel
        </button>
      </div>
    </form>
  );
}
