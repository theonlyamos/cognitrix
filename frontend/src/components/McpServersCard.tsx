import { useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Field } from '@/components/form';
import { cn } from '@/lib/utils';
import {
  KEEP_SECRET,
  isConnectedApp,
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
import { tr, trn } from '@/lib/i18n';

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
      .then((l) => setList({ ...l, servers: l.servers.filter((s) => !isConnectedApp(s)) }))
      .catch((e) => setLoadError(errorMessage(e, tr('ui.mcpServersCard.couldNotLoadMcpServers'))));

  useEffect(() => { void load(); }, []);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!list) return <p className="text-[13px] text-fg-dim">{tr('ui.mcpServersCard.loading')}</p>;

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
      setError(errorMessage(e, tr('ui.mcpServersCard.couldNotSaveTheServer')));
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
      setError(errorMessage(e, tr('ui.mcpServersCard.couldNotRemoveTheServer')));
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
      setError(errorMessage(e, tr('ui.mcpServersCard.couldNotChangeTheServer')));
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
        {tr('ui.mcpServersCard.mcpServersGiveAgentsExtra')}{' '}<a href="/settings/apps" className="text-accent-ink hover:underline">{tr('ui.mcpServersCard.connectedApps')}</a>{tr('ui.mcpServersCard.pickWhichOnesAnAgent')}
      </p>

      {list.servers.length === 0 ? (
        <p className="rounded-[10px] border border-line bg-bg px-3 py-3 text-[12.5px] text-fg-dim">{tr('ui.mcpServersCard.noServersYet')}</p>
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
                      {server.source === 'config_file' ? tr('ui.mcpServersCard.sharedMcpJson') : tr('ui.mcpServersCard.shared')}
                    </span>
                  )}
                  {!server.enabled && <span className="text-[11.5px] text-fg-dim">{tr('ui.mcpServersCard.off')}</span>}
                  <span className="flex-1" />
                  <button type="button" onClick={() => void test(server)} disabled={result === 'running'}
                    className="text-[12.5px] text-accent-ink hover:underline disabled:opacity-60">
                    {result === 'running' ? tr('ui.mcpServersCard.connecting') : tr('ui.mcpServersCard.test')}
                  </button>
                  {server.can_edit && (
                    <>
                      <button type="button" onClick={() => void toggle(server)} className="text-[12.5px] text-fg-dim hover:text-fg">
                        {server.enabled ? tr('ui.mcpServersCard.turnOff') : tr('ui.mcpServersCard.turnOn')}
                      </button>
                      <button type="button" onClick={() => { setError(null); setDraft(draftFrom(server)); }}
                        className="text-[12.5px] text-fg-dim hover:text-fg">
                        {tr('ui.mcpServersCard.edit')}
                      </button>
                      <button type="button" onClick={() => void remove(server)} disabled={busy}
                        className="text-[12.5px] text-danger-ink hover:underline disabled:opacity-60">
                        {tr('ui.mcpServersCard.remove')}
                      </button>
                    </>
                  )}
                </div>
                {server.description && <p className="mt-1 text-[12px] text-fg-dim">{server.description}</p>}
                {result && result !== 'running' && (
                  result.connected ? (
                    <div className="mt-2 text-[12px]">
                      <p className="text-fg-dim">{trn(result.tools.length, 'ui.count.tools_one', 'ui.count.tools_other')}</p>
                      <ul className="mt-1 space-y-0.5">
                        {result.tools.map((t) => (
                          <li key={t.name} className="flex gap-2">
                            <span className="font-mono">{t.name}</span>
                            <span className="text-fg-dim">{(t.asks_first ?? !t.read_only) ? tr('ui.mcpServersCard.asksFirst') : t.read_only ? tr('ui.mcpServersCard.readsOnly') : tr('ui.mcpServersCard.runsWithoutAsking')}</span>
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
          {tr('ui.mcpServersCard.addServer')}
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
    <Field label={label} hint={tr('ui.mcpServersCard.valuesAreNeverShownAgain')} composite>
      <div role="group" className="space-y-2">
        {pairs.map((p, i) => (
          <div key={i} className="flex gap-2">
            <Input aria-label={tr('ui.mcpServersCard.labelNameValue', { label, value: i + 1 })} value={p.key} placeholder={tr('ui.mcpServersCard.name')} onChange={(e) => set(i, { key: e.target.value })} />
            <Input
              aria-label={tr('ui.mcpServersCard.labelValueValue', { label, value: i + 1 })}
              type="password"
              autoComplete="off"
              value={p.value === KEEP_SECRET ? '' : p.value}
              placeholder={p.value === KEEP_SECRET ? tr('ui.mcpServersCard.savedLeaveBlankToKeep') : valuePlaceholder}
              onChange={(e) => set(i, { value: e.target.value === '' && p.value === KEEP_SECRET ? KEEP_SECRET : e.target.value })}
            />
            <button type="button" aria-label={tr('ui.mcpServersCard.removeLabelValue', { label: label.toLowerCase(), value: i + 1 })}
              onClick={() => onChange(pairs.length > 1 ? pairs.filter((_, j) => j !== i) : [{ key: '', value: '' }])}
              className="px-2 text-[13px] text-fg-dim hover:text-fg">
              ×
            </button>
          </div>
        ))}
        <button type="button" onClick={() => onChange([...pairs, { key: '', value: '' }])} className="text-[12.5px] text-accent-ink hover:underline">
          {tr('ui.mcpServersCard.addAnother')}
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
      aria-label={draft.id ? tr('ui.mcpServersCard.editName', { name: draft.name }) : tr('ui.mcpServersCard.addMcpServer')}
      className="space-y-3 rounded-[10px] border border-line bg-bg p-3"
      onSubmit={(e) => { e.preventDefault(); onSave(); }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={tr('ui.mcpServersCard.name_a9d3')} hint="letters, numbers, - and _" required>
          <Input value={draft.name} onChange={(e) => set({ name: e.target.value })} placeholder="notion" />
        </Field>
        <Field label={tr('ui.mcpServersCard.type')}>
          <Select value={draft.transport} onChange={(e) => set({ transport: e.target.value as McpTransport })}>
            <option value="http">{tr('ui.mcpServersCard.remoteHttp')}</option>
            <option value="sse">{tr('ui.mcpServersCard.remoteSseOlderServers')}</option>
            {(canAddLocal || stdio) && <option value="stdio">{tr('ui.mcpServersCard.localCommandStdio')}</option>}
          </Select>
        </Field>
      </div>
      <Field label={tr('ui.mcpServersCard.description')}>
        <Input value={draft.description} onChange={(e) => set({ description: e.target.value })} placeholder={tr('ui.mcpServersCard.whatItSFor')} />
      </Field>
      {stdio ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label={tr('ui.mcpServersCard.command')} required>
              <Input value={draft.command} onChange={(e) => set({ command: e.target.value })} placeholder="npx" />
            </Field>
            <Field label={tr('ui.mcpServersCard.arguments')} hint={tr('ui.mcpServersCard.separatedBySpaces')}>
              <Input value={draft.args} onChange={(e) => set({ args: e.target.value })} placeholder={tr('ui.mcpServersCard.yModelcontextprotocolServerMemory')} />
            </Field>
          </div>
          <PairRows label={tr('ui.mcpServersCard.environment')} pairs={draft.env} onChange={(env) => set({ env })} valuePlaceholder={tr('ui.mcpServersCard.value')} />
        </>
      ) : (
        <>
          <Field label={tr('ui.mcpServersCard.address')} required>
            <Input value={draft.url} onChange={(e) => set({ url: e.target.value })} placeholder="https://mcp.example.com/mcp" />
          </Field>
          <PairRows label={tr('ui.mcpServersCard.headers')} pairs={draft.headers} onChange={(headers) => set({ headers })} valuePlaceholder={tr('ui.mcpServersCard.bearer')} />
        </>
      )}
      {canAddShared && !draft.id && (
        <label className="flex items-center gap-2 text-[13px]">
          <input type="checkbox" checked={draft.shared} onChange={(e) => set({ shared: e.target.checked })} className="accent-[var(--accent)]" />
          {tr('ui.mcpServersCard.shareWithEveryoneOnThis')}
        </label>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={busy}
          className="h-9 rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60">
          {busy ? tr('ui.mcpServersCard.saving') : draft.id ? tr('ui.mcpServersCard.save') : tr('ui.mcpServersCard.add')}
        </button>
        <button type="button" onClick={onCancel} className="h-9 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
          {tr('ui.mcpServersCard.cancel')}
        </button>
      </div>
    </form>
  );
}
