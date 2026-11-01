import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Field } from '@/components/form';
import { Toggle } from '@/pages/Notifications';
import { cn } from '@/lib/utils';
import {
  deleteChannel,
  listChannels,
  relinkChannel,
  saveChannel,
  testChannel,
  type ChannelKind,
  type ChannelList,
  type ChannelUpdate,
  type ChatChannel,
} from '@/lib/channels';

interface AgentLite { id: string; name: string }

const LABEL: Record<ChannelKind, string> = { telegram: 'Telegram', slack: 'Slack' };

function StatusDot({ channel }: { channel: ChatChannel }) {
  const state = !channel.enabled
    ? { text: 'Off', tone: 'bg-fg-dim' }
    : channel.connected
      ? { text: 'Connected', tone: 'bg-accent' }
      : channel.status === 'error'
        ? { text: 'Error', tone: 'bg-danger' }
        : { text: 'Connecting', tone: 'bg-fg-dim animate-pulse' };
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-fg-dim">
      <span className={cn('h-1.5 w-1.5 rounded-full', state.tone)} aria-hidden />
      {state.text}
    </span>
  );
}

function Setup({ kind, manifest }: { kind: ChannelKind; manifest: Record<string, unknown> }) {
  const [copied, setCopied] = useState(false);
  if (kind === 'telegram') {
    return (
      <ol className="list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-fg-dim">
        <li>
          In Telegram, open{' '}
          <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">@BotFather</a>{' '}
          and send <span className="font-mono">/newbot</span>.
        </li>
        <li>Pick a name and username. BotFather replies with a token like <span className="font-mono">123456:ABC…</span>.</li>
        <li>Paste the token below. Then send the bot the code Cognitrix shows you.</li>
      </ol>
    );
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(manifest, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked: the steps still work by hand */ }
  };
  return (
    <ol className="list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-fg-dim">
      <li>
        Copy the{' '}
        <button type="button" onClick={copy} className="text-accent-ink hover:underline">
          {copied ? 'manifest (copied)' : 'app manifest'}
        </button>
        , then on{' '}
        <a href="https://api.slack.com/apps?new_app=1" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">api.slack.com/apps</a>{' '}
        choose Create New App › From a manifest and paste it.
      </li>
      <li>Install the app to your workspace and copy the Bot User OAuth Token (<span className="font-mono">xoxb-…</span>).</li>
      <li>
        Under Basic Information › App-Level Tokens, generate one with the <span className="font-mono">connections:write</span> scope
        (<span className="font-mono">xapp-…</span>).
      </li>
      <li>Paste both below, then open the app in Slack and send it the code Cognitrix shows you.</li>
    </ol>
  );
}

function ChannelPanel({
  channel, agents, manifest, onChange,
}: {
  channel: ChatChannel;
  agents: AgentLite[];
  manifest: Record<string, unknown>;
  onChange: (next: ChatChannel | null) => void;
}) {
  const kind = channel.kind;
  const label = LABEL[kind];
  const [botToken, setBotToken] = useState('');
  const [appToken, setAppToken] = useState('');
  const [editing, setEditing] = useState(!channel.configured);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const run = async (work: () => Promise<ChatChannel | null | void>, done?: string) => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const next = await work();
      if (next !== undefined) onChange(next);
      if (done) setNote(done);
      return true;
    } catch (e) {
      setError(errorMessage(e, `Could not update ${label}.`));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const update = (body: ChannelUpdate, done?: string) => run(() => saveChannel(kind, body), done);

  const connect = async () => {
    const body: ChannelUpdate = { bot_token: botToken.trim() };
    if (kind === 'slack') body.app_token = appToken.trim();
    if (await update(body)) {
      setBotToken('');
      setAppToken('');
      setEditing(false);
    }
  };

  const tokenForm = (
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void connect(); }}>
      <Field label={kind === 'slack' ? 'BOT TOKEN (xoxb-…)' : 'BOT TOKEN'}>
        <Input
          type="password"
          autoComplete="off"
          aria-label={`${label} bot token`}
          placeholder={channel.bot_token_hint ? `saved ${channel.bot_token_hint}` : kind === 'slack' ? 'xoxb-…' : '123456:ABC…'}
          value={botToken}
          onChange={(e) => setBotToken(e.target.value)}
          disabled={busy}
        />
      </Field>
      {kind === 'slack' && (
        <Field label="APP-LEVEL TOKEN (xapp-…)">
          <Input
            type="password"
            autoComplete="off"
            aria-label="Slack app-level token"
            placeholder="xapp-…"
            value={appToken}
            onChange={(e) => setAppToken(e.target.value)}
            disabled={busy}
          />
        </Field>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy || !botToken.trim() || (kind === 'slack' && !appToken.trim())}
          className="h-10 rounded-lg bg-accent px-3 text-[13px] font-medium text-[#0b0c0e] disabled:opacity-60"
        >
          {busy ? 'Checking…' : channel.configured ? 'Save tokens' : `Connect ${label}`}
        </button>
        {channel.configured && (
          <button type="button" onClick={() => setEditing(false)} className="h-10 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
            Cancel
          </button>
        )}
      </div>
    </form>
  );

  return (
    <div className="rounded-[12px] border border-line bg-bg p-3.5">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[14px] font-medium">{label}</div>
          {channel.configured && <div className="truncate font-mono text-[11.5px] text-fg-dim">{channel.bot_name}</div>}
        </div>
        {channel.configured && (
          <div className="flex items-center gap-3">
            <StatusDot channel={channel} />
            <Toggle
              label={`${label} on`}
              checked={channel.enabled}
              disabled={busy}
              onChange={(v) => void update({ enabled: v })}
            />
          </div>
        )}
      </div>

      {!channel.configured ? (
        <div className="space-y-3">
          <Setup kind={kind} manifest={manifest} />
          {tokenForm}
        </div>
      ) : editing ? (
        tokenForm
      ) : (
        <div className="space-y-3">
          {channel.enabled && channel.status === 'error' && channel.last_error && (
            <p role="alert" className="text-[12.5px] text-danger-ink">{channel.last_error}</p>
          )}

          {channel.linked ? (
            <p className="text-[12.5px] text-fg-dim">
              Linked to {channel.peer_name ? <span className="text-fg">{channel.peer_name}</span> : 'your chat'}. Only that chat is answered.
            </p>
          ) : channel.link_code ? (
            <div className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[12.5px]">
              <p className="text-fg-dim">
                {kind === 'telegram' ? 'Open the bot and send this code' : 'Open the app in Slack (Apps › Cognitrix › Messages) and send this code'}:
              </p>
              <div className="mt-1.5 flex flex-wrap items-center gap-3">
                <span className="select-all font-mono text-[18px] tracking-[0.18em]" aria-label="Link code">{channel.link_code}</span>
                {channel.link_url && (
                  <a href={channel.link_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                    Open {channel.bot_name} in Telegram
                  </a>
                )}
              </div>
              <p className="mt-1.5 text-[11.5px] text-fg-dim">The code works for 30 minutes. This page updates once the chat is linked.</p>
            </div>
          ) : (
            <p className="text-[12.5px] text-fg-dim">
              The link code expired.{' '}
              <button type="button" onClick={() => void run(() => relinkChannel(kind))} className="text-accent-ink hover:underline">Get a new code</button>
            </p>
          )}

          <Field label="AGENT" hint="new conversations use it; change it in chat with /agent">
            <Select
              value={channel.agent_id}
              disabled={busy}
              onChange={(e) => void update({ agent_id: e.target.value }, 'Agent saved.')}
              aria-label={`${label} agent`}
            >
              <option value="">Your first agent</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          </Field>

          <div className="divide-y divide-line">
            <div className="flex items-center justify-between gap-4 py-2">
              <div>
                <div className="text-[13px]">Agent alerts</div>
                <div className="text-[12px] text-fg-dim">Messages agents send with Notify Me.</div>
              </div>
              <Toggle label={`${label} agent alerts`} checked={channel.send_alerts} disabled={busy}
                onChange={(v) => void update({ send_alerts: v })} />
            </div>
            <div className="flex items-center justify-between gap-4 py-2">
              <div>
                <div className="text-[13px]">Task approvals</div>
                <div className="text-[12px] text-fg-dim">Approve or deny with buttons. Chats started here always ask here.</div>
              </div>
              <Toggle label={`${label} task approvals`} checked={channel.send_approvals} disabled={busy}
                onChange={(v) => void update({ send_approvals: v })} />
            </div>
          </div>

          <div className="flex flex-wrap gap-2 text-[12.5px]">
            {channel.linked && (
              <button type="button" disabled={busy || !channel.connected}
                onClick={() => void run(() => testChannel(kind), 'Test message sent.')}
                className="h-9 rounded-lg border border-line px-3 hover:bg-panel-2 disabled:opacity-60">
                Send test message
              </button>
            )}
            {channel.linked && (
              <button type="button" disabled={busy} onClick={() => void run(() => relinkChannel(kind))}
                className="h-9 rounded-lg border border-line px-3 hover:bg-panel-2 disabled:opacity-60">
                Link a different chat
              </button>
            )}
            <button type="button" disabled={busy} onClick={() => setEditing(true)}
              className="h-9 rounded-lg border border-line px-3 hover:bg-panel-2 disabled:opacity-60">
              Change token
            </button>
            <button type="button" disabled={busy}
              onClick={() => void run(async () => { await deleteChannel(kind); return null; }, `${label} disconnected.`)}
              className="h-9 rounded-lg px-3 text-danger-ink hover:underline disabled:opacity-60">
              Disconnect
            </button>
          </div>
        </div>
      )}

      <div aria-live="polite" className="mt-2 min-h-[1rem] text-[12px]">
        {error ? <span role="alert" className="text-danger-ink">{error}</span> : note ? <span className="text-fg-dim">{note}</span> : null}
      </div>
    </div>
  );
}

/** Settings › Chat apps: talk to agents from Telegram or Slack and get
 *  Inbox alerts and approvals there. Bots connect out from the engine, so
 *  the desktop app needs no public address. */
export function ChatChannelsCard() {
  const [list, setList] = useState<ChannelList | null>(null);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = () =>
    listChannels()
      .then(setList)
      .catch((e) => setLoadError(errorMessage(e, 'Could not load chat apps.')));

  useEffect(() => {
    void load();
    api.get<AgentLite[]>('/agents').then((r) => setAgents(r.data ?? [])).catch(() => {});
  }, []);

  // While a code is waiting or a bot is connecting, refresh to show the change.
  const waiting = list?.channels.some((c) => c.configured && c.enabled && (!c.linked || !c.connected));
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => { void listChannels().then(setList).catch(() => {}); }, 4000);
    return () => clearInterval(timer);
  }, [waiting]);

  useEffect(() => {
    if (list && window.location.hash === '#chat-apps') {
      document.getElementById('chat-apps')?.scrollIntoView({ block: 'start' });
    }
  }, [list]);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!list) return <p className="text-[13px] text-fg-dim">Loading…</p>;

  const replace = (kind: ChannelKind, next: ChatChannel | null) => {
    if (next === null) {
      void load();
      return;
    }
    setList((current) => current && {
      ...current,
      channels: current.channels.map((c) => (c.kind === kind ? next : c)),
    });
  };

  return (
    <div className="space-y-3">
      <p className="text-[13px] leading-relaxed text-fg-dim">
        Chat with your agents from Telegram or Slack, and get agent alerts and task approvals there. You create the bot,
        so messages go only between your bot and this engine; nothing needs a public address.
      </p>
      {!list.available && (
        <p className="rounded-[10px] border border-line bg-bg px-3 py-2 text-[12.5px] text-fg-dim">
          Chat apps are turned off on this engine (<span className="font-mono">COGNITRIX_CHANNELS=0</span>).
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {list.channels.map((channel) => (
          <ChannelPanel
            key={channel.kind}
            channel={channel}
            agents={agents}
            manifest={list.slack_manifest}
            onChange={(next) => replace(channel.kind, next)}
          />
        ))}
      </div>
    </div>
  );
}
