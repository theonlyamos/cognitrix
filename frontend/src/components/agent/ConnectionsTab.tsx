import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import { listChannels, saveChannel, type ChatChannel } from '@/lib/channels';
import { listCredentials, type Credential } from '@/lib/credentials';
import { listMcpServers, type McpServer } from '@/lib/mcp';
import type { TabProps } from '@/lib/agentDraft';
import { Card, buttonClass, linkButtonClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';

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
    listCredentials().then(setCreds).catch((e) => { setCreds([]); setCredsError(errorMessage(e, "Couldn't load saved sign-ins.")); });
    listChannels().then((l) => setChannels(l.channels || [])).catch(() => setChannels([]));
    import('@/lib/api').then(({ api }) => api.get<{ id: string }[]>('/agents'))
      .then((r) => setFirstAgentId(r.data?.[0]?.id || '')).catch(() => {});
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
      setChannelError(errorMessage(e, 'Could not change the chat app.'));
    } finally {
      setChannelBusy('');
    }
  };

  return (
    <div className="flex flex-col gap-[18px]">
      <Card title="MCP servers" description="Servers you added in Settings. Turn on the ones this agent may use.">
        <div className="overflow-hidden rounded-lg border border-line">
          {servers === null ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim">Loading…</p>
          ) : servers.length === 0 && missing.length === 0 ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim">No MCP servers yet.</p>
          ) : (
            <>
              {servers.map((s) => {
                const on = selected.includes(s.ref) || selected.includes(s.name);
                const status = !s.enabled ? 'off' : s.connected ? 'connected' : 'not connected';
                return (
                  <label key={s.ref} className="grid cursor-pointer grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-3 border-t border-line px-3.5 py-3 first:border-t-0">
                    <input type="checkbox" checked={on} onChange={(e) => toggleServer(s, e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
                    <span className="min-w-0">
                      <span className="block font-mono text-[13.5px]">
                        {s.name} <span className="font-sans text-[12px] text-fg-dim">{s.shared ? 'shared' : 'yours'} · {s.transport}</span>
                      </span>
                      {s.description && <span className="block truncate text-[12.5px] text-fg-dim">{s.description}</span>}
                    </span>
                    <span className={cn('inline-flex items-center gap-1.5 text-[12.5px]',
                      status === 'connected' ? 'text-ok' : status === 'off' ? 'text-fg-dim' : 'text-[#a15c00] dark:text-[#e5b567]')}>
                      <span aria-hidden className="h-[7px] w-[7px] rounded-full bg-current" />{status}
                    </span>
                  </label>
                );
              })}
              {missing.map((r) => (
                <div key={r} className="flex items-center justify-between gap-2 border-t border-line px-3.5 py-3 text-[13px] first:border-t-0">
                  <span className="text-fg-dim"><span className="font-mono">{r}</span> isn't available any more</span>
                  <button type="button" onClick={() => update({ mcpServers: selected.filter((x) => x !== r) })} className="text-danger-ink hover:underline">Remove</button>
                </div>
              ))}
            </>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Link to="/settings/access#mcp-servers" className={buttonClass}>+ Add a server</Link>
          <span className="text-[12.5px] text-fg-dim">Tools a server doesn't mark read-only ask for approval first.</span>
        </div>
      </Card>

      <Card title="Saved sign-ins">
        <p className="-mt-2 text-[13px] text-fg-dim">
          The agent uses these by name with the Http Request tool. It never sees the secret, and each one only works on its own sites.
        </p>
        <div className="mt-3.5 flex flex-col gap-2" role="radiogroup" aria-label="Which saved sign-ins">
          <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
            <input type="radio" name="cred-scope" checked={allowed === null} onChange={() => update({ credentials: null })} className="h-4 w-4 accent-[var(--accent)]" />
            All of your saved sign-ins
          </label>
          <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
            <input type="radio" name="cred-scope" checked={allowed !== null} onChange={() => update({ credentials: allowed ?? [] })} className="h-4 w-4 accent-[var(--accent)]" />
            Only the ones I pick
          </label>
        </div>
        {allowed !== null && (
          <div className="mt-3 overflow-hidden rounded-lg border border-line">
            {creds === null ? (
              <p className="px-3.5 py-3 text-[13px] text-fg-dim">Loading…</p>
            ) : creds.length === 0 ? (
              <p className="px-3.5 py-3 text-[13px] text-fg-dim">{credsError || 'No saved sign-ins yet.'}</p>
            ) : creds.map((c) => (
              <label key={c.id} className="grid cursor-pointer grid-cols-[22px_minmax(0,1fr)] items-center gap-x-3 border-t border-line px-3.5 py-2.5 first:border-t-0 sm:grid-cols-[22px_200px_minmax(0,1fr)_80px]">
                <input type="checkbox" checked={allowed.includes(c.name)} onChange={(e) => toggleCred(c.name, e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
                <span className="truncate font-mono text-[13px]">{c.name}</span>
                <span className="col-start-2 truncate font-mono text-[12px] text-fg-dim sm:col-start-auto">{c.allowed_hosts.join(', ') || 'no sites'}</span>
                <span className="hidden text-[12.5px] text-fg-dim sm:block">{c.kind === 'basic' ? 'password' : c.kind}</span>
              </label>
            ))}
          </div>
        )}
        <Link to="/settings/access" className={cn(linkButtonClass, 'mt-3 inline-block')}>Manage saved sign-ins</Link>
      </Card>

      <Card title="Chat apps" description="Talk to an agent from your phone.">
        <div className="overflow-hidden rounded-lg border border-line">
          {channels === null ? (
            <p className="px-3.5 py-3 text-[13px] text-fg-dim">Loading…</p>
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
                    Use this agent
                  </button>
                ) : (
                  <Link to="/settings/chat-apps" className={buttonClass}>{c.configured ? 'Settings' : 'Set up'}</Link>
                )}
              </div>
            );
          })}
        </div>
        {channelError && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{channelError}</p>}
        {channels?.some((c) => c.configured) && (
          <p className="mt-2.5 text-[12.5px] text-fg-dim">“Use this agent” switches the app right away; it isn't part of Save.</p>
        )}
      </Card>
    </div>
  );
}
