import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import { listChannels, saveChannel, type ChatChannel } from '@/lib/channels';
import { listCredentials, type Credential } from '@/lib/credentials';
import { listMcpServerTools, listMcpServers, type McpServer, type McpTestResult } from '@/lib/mcp';
import { appLabel } from '@/lib/apps';
import type { TabProps, ToolLevel } from '@/lib/agentDraft';
import { Card, Check, buttonClass, inputClass, linkButtonClass } from '@/components/agent/ui';
import type { KnowledgeCollection } from '@/pages/Knowledge';
import { cn } from '@/lib/utils';
import { tr, trn } from '@/lib/i18n';

const APP_LABEL: Record<string, string> = { telegram: 'Telegram', slack: 'Slack' };

export default function ConnectionsTab({ agent, draft, update }: TabProps) {
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [creds, setCreds] = useState<Credential[] | null>(null);
  const [credsError, setCredsError] = useState('');
  const [channels, setChannels] = useState<ChatChannel[] | null>(null);
  const [channelBusy, setChannelBusy] = useState('');
  const [channelError, setChannelError] = useState('');
  const [firstAgentId, setFirstAgentId] = useState('');

  useEffect(() => {
    listMcpServers().then((l) => setServers(Array.isArray(l?.servers) ? l.servers : [])).catch(() => setServers([]));
    listCredentials().then(setCreds).catch((e) => { setCreds([]); setCredsError(errorMessage(e, tr('ui.agent.connectionsTab.couldnTLoadSavedSign'))); });
    listChannels().then((l) => setChannels(l.channels || [])).catch(() => setChannels([]));
    import('@/lib/api').then(({ api }) => api.get<{ id: string }[]>('/agents'))
      .then((r) => setFirstAgentId(r.data?.[0]?.id || '')).catch(() => {});
  }, []);

  const [collections, setCollections] = useState<KnowledgeCollection[] | null>(null);
  useEffect(() => {
    import('@/lib/api').then(({ api }) => api.get<KnowledgeCollection[]>('/knowledge'))
      .then((r) => setCollections(Array.isArray(r.data) ? r.data : [])).catch(() => setCollections([]));
  }, []);

  const selected = draft.mcpServers;
  const toggleServer = (s: McpServer, on: boolean) => update({
    mcpServers: on ? [...selected, s.ref] : selected.filter((r) => r !== s.ref && r !== s.name),
  });
  const missing = selected.filter((r) => servers && !servers.some((s) => s.ref === r || s.name === r));

  const allowed = draft.credentials;
  const toggleCred = (name: string, on: boolean) => {
    const cur = allowed || [];
    update({ credentials: on ? [...cur, name] : cur.filter((n) => n !== name) });
  };

  const pointHere = async (c: ChatChannel) => {
    setChannelBusy(c.kind);
    setChannelError('');
    try {
      const saved = await saveChannel(c.kind, { agent_id: agent.id });
      setChannels((cur) => (cur || []).map((x) => (x.kind === c.kind ? saved : x)));
    } catch (e) {
      setChannelError(errorMessage(e, tr('ui.agent.connectionsTab.couldNotChangeTheChat')));
    } finally {
      setChannelBusy('');
    }
  };

  return (
    <div className="flex flex-col gap-[18px]">
      <Card title={tr('ui.agent.connectionsTab.knowledge')} description={tr('ui.agent.connectionsTab.documentCollectionsThisAgentMay')}>
        <div className="overflow-hidden rounded-lg border border-line">
          {collections === null ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim">{tr('ui.agent.connectionsTab.loading')}</p>
          ) : collections.length === 0 ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim">
              {tr('ui.agent.connectionsTab.noCollectionsYet')}{' '}<Link to="/knowledge" className="text-accent-ink hover:underline">{tr('ui.agent.connectionsTab.createOneOnTheKnowledge')}</Link>.
            </p>
          ) : collections.map((c) => (
            <div key={c.id} className="border-t border-line px-3.5 py-2.5 first:border-t-0">
              <Check
                checked={draft.knowledge.includes(c.id)}
                onChange={(on) => update({ knowledge: on ? [...draft.knowledge, c.id] : draft.knowledge.filter((id) => id !== c.id) })}
                label={c.name}
                detail={trn(c.documents.length, 'ui.count.documents_one', 'ui.count.documents_other')}
              />
            </div>
          ))}
        </div>
      </Card>

      <Card title={tr('ui.agent.connectionsTab.appsAndMcpServers')} description={tr('ui.agent.connectionsTab.appsYouConnectedAndServers')}>
        <div className="overflow-hidden rounded-lg border border-line">
          {servers === null ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim">{tr('ui.agent.connectionsTab.loading')}</p>
          ) : servers.length === 0 && missing.length === 0 ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim">{tr('ui.agent.connectionsTab.noAppsOrMcpServers')}</p>
          ) : (
            <>
              {servers.map((s) => {
                const on = selected.includes(s.ref) || selected.includes(s.name);
                const app = Boolean(s.catalog_id) || s.auth_type === 'oauth';
                const status = !s.enabled ? 'off'
                  : s.status === 'needs_reauth' ? 'needs reconnect'
                    : app && s.status !== 'connected' ? 'not signed in'
                      : s.connected || (app && s.status === 'connected') ? 'connected' : 'not connected';
                return (
                  <div key={s.ref} className="border-t border-line first:border-t-0">
                    <label className="grid cursor-pointer grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-3 px-3.5 py-3">
                      <input type="checkbox" checked={on} onChange={(e) => toggleServer(s, e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
                      <span className="min-w-0">
                        <span className={cn('block text-[13.5px]', !s.catalog_id && 'font-mono')}>
                          {appLabel(s.catalog_id, s.name)}{' '}
                          <span className="font-sans text-[12px] text-fg-dim">
                            {s.catalog_id ? (s.account || tr('ui.agent.connectionsTab.app')) : `${s.shared ? 'shared' : 'yours'} · ${s.transport}`}
                          </span>
                        </span>
                        {s.description && <span className="block truncate text-[12.5px] text-fg-dim">{s.description}</span>}
                      </span>
                      <span className={cn('inline-flex items-center gap-1.5 text-[12.5px]',
                        status === 'connected' ? 'text-ok' : status === 'off' ? 'text-fg-dim' : 'text-warn-ink')}>
                        <span aria-hidden className="h-[7px] w-[7px] rounded-full bg-current" />{status}
                      </span>
                    </label>
                    {on && s.enabled && (
                      <ToolPermissions
                        server={s}
                        label={appLabel(s.catalog_id, s.name)}
                        levels={draft.toolPermissions}
                        onChange={(toolPermissions) => update({ toolPermissions })}
                      />
                    )}
                  </div>
                );
              })}
              {missing.map((r) => (
                <div key={r} className="flex items-center justify-between gap-2 border-t border-line px-3.5 py-3 text-[13px] first:border-t-0">
                  <span className="text-fg-dim"><span className="font-mono">{r}</span>{' '}{tr('ui.agent.connectionsTab.isnTAvailableAnyMore')}</span>
                  <button type="button" onClick={() => update({ mcpServers: selected.filter((x) => x !== r) })} className="text-danger-ink hover:underline">{tr('ui.agent.connectionsTab.remove')}</button>
                </div>
              ))}
            </>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Link to="/settings/apps" className={buttonClass}>{tr('ui.agent.connectionsTab.connectAnApp')}</Link>
          <Link to="/settings/access#mcp-servers" className={linkButtonClass}>{tr('ui.agent.connectionsTab.addAnMcpServer')}</Link>
          <span className="text-[12.5px] text-fg-dim">{tr('ui.agent.connectionsTab.toolsThatOnlyReadRun')}</span>
        </div>
      </Card>

      <Card title={tr('ui.agent.connectionsTab.savedSignIns')}>
        <p className="-mt-2 text-[13px] text-fg-dim">
          {tr('ui.agent.connectionsTab.theAgentUsesTheseBy')}
        </p>
        <div className="mt-3.5 flex flex-col gap-2" role="radiogroup" aria-label={tr('ui.agent.connectionsTab.whichSavedSignIns')}>
          <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
            <input type="radio" name="cred-scope" checked={allowed === null} onChange={() => update({ credentials: null })} className="h-4 w-4 accent-[var(--accent)]" />
            {tr('ui.agent.connectionsTab.allOfYourSavedSign')}
          </label>
          <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
            <input type="radio" name="cred-scope" checked={allowed !== null} onChange={() => update({ credentials: allowed ?? [] })} className="h-4 w-4 accent-[var(--accent)]" />
            {tr('ui.agent.connectionsTab.onlyTheOnesIPick')}
          </label>
        </div>
        {allowed !== null && (
          <div className="mt-3 overflow-hidden rounded-lg border border-line">
            {creds === null ? (
              <p className="px-3.5 py-3 text-[13px] text-fg-dim">{tr('ui.agent.connectionsTab.loading')}</p>
            ) : creds.length === 0 ? (
              <p className="px-3.5 py-3 text-[13px] text-fg-dim">{credsError || tr('ui.agent.connectionsTab.noSavedSignInsYet')}</p>
            ) : creds.map((c) => (
              <label key={c.id} className="grid cursor-pointer grid-cols-[22px_minmax(0,1fr)] items-center gap-x-3 border-t border-line px-3.5 py-2.5 first:border-t-0 sm:grid-cols-[22px_200px_minmax(0,1fr)_80px]">
                <input type="checkbox" checked={allowed.includes(c.name)} onChange={(e) => toggleCred(c.name, e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
                <span className="truncate font-mono text-[13px]">{c.name}</span>
                <span className="col-start-2 truncate font-mono text-[12px] text-fg-dim sm:col-start-auto">{c.allowed_hosts.join(', ') || tr('ui.agent.connectionsTab.noSites')}</span>
                <span className="hidden text-[12.5px] text-fg-dim sm:block">{c.kind === 'basic' ? tr('ui.agent.connectionsTab.password') : c.kind}</span>
              </label>
            ))}
          </div>
        )}
        <Link to="/settings/access" className={cn(linkButtonClass, 'mt-3 inline-block')}>{tr('ui.agent.connectionsTab.manageSavedSignIns')}</Link>
      </Card>

      <Card title={tr('ui.agent.connectionsTab.chatApps')} description={tr('ui.agent.connectionsTab.talkToAnAgentFrom')}>
        <div className="overflow-hidden rounded-lg border border-line">
          {channels === null ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim">{tr('ui.agent.connectionsTab.loading')}</p>
          ) : channels.map((c) => {
            const target = c.agent_id || firstAgentId;
            const here = target === agent.id;
            const state = !c.configured ? 'Not set up'
              : here ? `${c.bot_name || APP_LABEL[c.kind]} sends your messages to this agent${c.linked ? '' : ' (waiting for you to link your chat)'}`
                : `${c.bot_name || APP_LABEL[c.kind]} sends your messages to another agent`;
            return (
              <div key={c.kind} className="flex flex-wrap items-center gap-3 border-t border-line px-3.5 py-3 first:border-t-0">
                <span className="w-[84px] text-[14px] font-semibold">{APP_LABEL[c.kind] || c.kind}</span>
                <span className="min-w-[200px] flex-1 text-[13px] text-fg-dim">{state}</span>
                {c.configured && !here ? (
                  <button type="button" disabled={channelBusy === c.kind} onClick={() => void pointHere(c)} className={buttonClass}>
                    {tr('ui.agent.connectionsTab.useThisAgent')}
                  </button>
                ) : (
                  <Link to="/settings/chat-apps" className={buttonClass}>{c.configured ? tr('ui.agent.connectionsTab.settings') : tr('ui.agent.connectionsTab.setUp')}</Link>
                )}
              </div>
            );
          })}
        </div>
        {channelError && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{channelError}</p>}
        {channels?.some((c) => c.configured) && (
          <p className="mt-2.5 text-[12.5px] text-fg-dim">{tr('ui.agent.connectionsTab.useThisAgentSwitchesThe')}</p>
        )}
      </Card>
    </div>
  );
}

type AppToolLevel = 'allow' | 'told' | 'ask' | 'handoff' | 'deny';

// "When I ask": runs in a chat you are in, asks when the agent works on its
// own. "Hand off": never runs; what was prepared goes to your Inbox.
const LEVELS: { value: AppToolLevel; label: string }[] = [
  { value: 'allow', get label() { return tr('ui.agent.connectionsTab.allow'); } },
  { value: 'told', get label() { return tr('ui.agent.connectionsTab.whenIAsk'); } },
  { value: 'ask', get label() { return tr('ui.agent.connectionsTab.ask'); } },
  { value: 'handoff', get label() { return tr('ui.agent.connectionsTab.handOff'); } },
  { value: 'deny', get label() { return tr('ui.agent.connectionsTab.off'); } },
];
const isAppLevel = (v: unknown): v is AppToolLevel => LEVELS.some((l) => l.value === v);

/** Allow / Ask / Off for each of a server's tools, for this agent.
 *  Matching the default stores nothing, so the default can change later. */
function ToolPermissions({ server, label, levels, onChange }: {
  server: McpServer;
  label: string;
  levels: Record<string, ToolLevel>;
  onChange: (next: Record<string, ToolLevel>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<McpTestResult | null>(null);

  useEffect(() => {
    if (!open || result) return;
    listMcpServerTools(server.ref)
      .then(setResult)
      .catch((e) => setResult({ connected: false, tools: [], error: errorMessage(e, tr('ui.agent.connectionsTab.couldnTLoadTheTools')) }));
  }, [open, result, server.ref]);

  const set = (tool: string, fallback: 'allow' | 'ask', value: AppToolLevel) => {
    const next = { ...levels };
    if (value === fallback) delete next[tool];
    else next[tool] = value;
    onChange(next);
  };

  const custom = result?.tools.filter((t) => t.tool && levels[t.tool] && levels[t.tool] !== 'auto').length ?? 0;

  return (
    <div className="border-t border-line bg-bg px-3.5 py-2.5">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="text-[12.5px] text-accent-ink hover:underline">
        {open ? tr('ui.agent.connectionsTab.hide') : tr('ui.agent.connectionsTab.choose')}{' '}{tr('ui.agent.connectionsTab.what')}{' '}{label}{' '}{tr('ui.agent.connectionsTab.toolsMayDo')}{custom ? tr('ui.agent.connectionsTab.customChanged', { custom }) : ''}
      </button>
      {open && (
        !result ? (
          <p className="mt-2 text-[12.5px] text-fg-dim">{tr('ui.agent.connectionsTab.connecting')}</p>
        ) : !result.connected ? (
          <p role="alert" className="mt-2 text-[12.5px] text-warn-ink">
            {result.error}{' '}
            {(server.catalog_id || server.auth_type === 'oauth') && <Link to="/settings/apps" className="text-accent-ink hover:underline">{tr('ui.agent.connectionsTab.connectedApps')}</Link>}
          </p>
        ) : result.tools.length === 0 ? (
          <p className="mt-2 text-[12.5px] text-fg-dim">{tr('ui.agent.connectionsTab.thisServerHasNoTools')}</p>
        ) : (
          <ul className="mt-2 divide-y divide-line">
            {result.tools.map((t) => {
              const tool = t.tool || t.name;
              const fallback = t.default || (t.asks_first ? 'ask' : 'allow');
              const stored = levels[tool];
              const value: AppToolLevel = isAppLevel(stored) ? stored : fallback;
              return (
                <li key={t.name} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2">
                  <div className="min-w-0 flex-1 basis-[200px]">
                    <div className="font-mono text-[12.5px]">{t.name}</div>
                    <div className="truncate text-[11.5px] text-fg-dim" title={t.description}>
                      {t.read_only ? tr('ui.agent.connectionsTab.readsOnly') : fallback === 'ask' ? tr('ui.agent.connectionsTab.changesOrSendsSomething') : tr('ui.agent.connectionsTab.makesChanges')}
                      {t.description ? ` · ${t.description}` : ''}
                    </div>
                  </div>
                  <select aria-label={tr('ui.agent.connectionsTab.namePermission', { name: t.name })} value={value}
                    onChange={(e) => set(tool, fallback, e.target.value as AppToolLevel)}
                    className={cn(inputClass, 'h-10 w-[150px] px-2 text-[13px] md:h-9', value === 'deny' && 'text-danger-ink')}>
                    {LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
                  </select>
                </li>
              );
            })}
          </ul>
        )
      )}
    </div>
  );
}
