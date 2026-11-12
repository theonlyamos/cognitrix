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

const LABEL: Record<ChannelKind, string> = { telegram: 'Telegram', slack: 'Slack', discord: 'Discord', teams: 'Microsoft Teams', whatsapp: 'WhatsApp' };

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
  if (kind === 'discord') {
    return (
      <ol className="list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-fg-dim">
        <li>
          Open the{' '}
          <a href="https://discord.com/developers/applications" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Discord Developer Portal</a>,
          choose New Application, then open its Bot page.
        </li>
        <li>Press Reset Token and copy the token. No extra permissions or intents are needed.</li>
        <li>Paste the token below. Cognitrix then shows a link that adds the bot to one of your servers, which Discord needs before you can message it.</li>
        <li>Send the bot a direct message with the code Cognitrix shows you.</li>
      </ol>
    );
  }
  if (kind === 'whatsapp') {
    return (
      <div className="space-y-2">
        <p className="rounded-[10px] border border-line bg-bg px-3 py-2 text-[12.5px] leading-relaxed text-fg-dim">
          WhatsApp sends messages to the bot, so this needs Cognitrix running on a server with a public
          <span className="font-mono"> https://</span> address. It does not work from the desktop app on its own.
        </p>
        <ol className="list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-fg-dim">
          <li>
            On{' '}
            <a href="https://developers.facebook.com/apps" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">developers.facebook.com</a>,
            create a Business app and add the WhatsApp product. Meta gives you a test number, or you can add your own.
          </li>
          <li>From WhatsApp › API Setup copy the Phone number ID and an access token. For a token that doesn't expire, create a system user in Business Settings.</li>
          <li>From App settings › Basic copy the App secret. Paste all three below.</li>
          <li>Cognitrix then shows a callback URL and verify token. Paste them under WhatsApp › Configuration and subscribe to the <span className="font-mono">messages</span> field.</li>
          <li>Send the bot's number the code Cognitrix shows you.</li>
        </ol>
      </div>
    );
  }
  if (kind === 'teams') {
    return (
      <div className="space-y-2">
        <p className="rounded-[10px] border border-line bg-bg px-3 py-2 text-[12.5px] leading-relaxed text-fg-dim">
          Teams sends messages to the bot, so this needs Cognitrix running on a server with a public
          <span className="font-mono"> https://</span> address. It does not work from the desktop app on its own.
        </p>
        <ol className="list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-fg-dim">
          <li>
            In the{' '}
            <a href="https://portal.azure.com/#create/Microsoft.AzureBot" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Azure portal</a>,
            create an Azure Bot with a single-tenant app, and add the Microsoft Teams channel to it.
          </li>
          <li>Copy the Microsoft App ID and the tenant ID from its Configuration page, and create a client secret.</li>
          <li>Paste all three below. Cognitrix then shows the messaging endpoint to paste back into the bot's Configuration page.</li>
          <li>Add the bot to Teams (a Teams app package with the same App ID), then send it the code Cognitrix shows you in a direct chat.</li>
        </ol>
      </div>
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
      <li>To use it in a channel, invite the app there and @mention it. It replies in a thread.</li>
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
  const [appId, setAppId] = useState(channel.app_id ?? '');
  const [tenantId, setTenantId] = useState(channel.tenant_id ?? '');
  const [copiedUrl, setCopiedUrl] = useState(false);
  const [appSecret, setAppSecret] = useState('');
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
    if (kind === 'teams') {
      body.app_id = appId.trim();
      body.tenant_id = tenantId.trim();
    }
    if (kind === 'whatsapp') {
      body.app_id = appId.trim();
      if (appSecret.trim()) body.app_secret = appSecret.trim();
    }
    if (await update(body)) {
      setBotToken('');
      setAppToken('');
      setAppSecret('');
      setEditing(false);
    }
  };

  const missing = !botToken.trim()
    || (kind === 'slack' && !appToken.trim())
    || (kind === 'teams' && (!appId.trim() || !tenantId.trim()))
    || (kind === 'whatsapp' && (!appId.trim() || (!appSecret.trim() && !channel.configured)));
  const endpoint = channel.webhook_path ? `${window.location.origin}${channel.webhook_path}` : '';
  const copyEndpoint = async () => {
    try {
      await navigator.clipboard.writeText(endpoint);
      setCopiedUrl(true);
      setTimeout(() => setCopiedUrl(false), 2000);
    } catch { /* clipboard blocked: the address can be selected by hand */ }
  };

  const tokenForm = (
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void connect(); }}>
      {kind === 'teams' && (
        <>
          <Field label="MICROSOFT APP ID">
            <Input autoComplete="off" aria-label="Teams App ID" placeholder="00000000-0000-0000-0000-000000000000"
              value={appId} onChange={(e) => setAppId(e.target.value)} disabled={busy} />
          </Field>
          <Field label="TENANT ID">
            <Input autoComplete="off" aria-label="Teams tenant ID" placeholder="00000000-0000-0000-0000-000000000000"
              value={tenantId} onChange={(e) => setTenantId(e.target.value)} disabled={busy} />
          </Field>
        </>
      )}
      {kind === 'whatsapp' && (
        <Field label="PHONE NUMBER ID">
          <Input autoComplete="off" inputMode="numeric" aria-label="WhatsApp phone number ID" placeholder="106540352242922"
            value={appId} onChange={(e) => setAppId(e.target.value)} disabled={busy} />
        </Field>
      )}
      <Field label={kind === 'slack' ? 'BOT TOKEN (xoxb-…)' : kind === 'teams' ? 'CLIENT SECRET' : kind === 'whatsapp' ? 'ACCESS TOKEN' : 'BOT TOKEN'}>
        <Input
          type="password"
          autoComplete="off"
          aria-label={kind === 'teams' ? 'Teams client secret' : kind === 'whatsapp' ? 'WhatsApp access token' : `${label} bot token`}
          placeholder={channel.bot_token_hint ? `saved ${channel.bot_token_hint}` : kind === 'slack' ? 'xoxb-…' : kind === 'teams' ? 'client secret value' : kind === 'whatsapp' ? 'EAAG…' : '123456:ABC…'}
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
      {kind === 'whatsapp' && (
        <Field label="APP SECRET">
          <Input type="password" autoComplete="off" aria-label="WhatsApp app secret"
            placeholder={channel.configured ? 'saved, leave empty to keep it' : 'from App settings › Basic'}
            value={appSecret} onChange={(e) => setAppSecret(e.target.value)} disabled={busy} />
        </Field>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy || missing}
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
    <div>
      {channel.configured && (
        <div className="mb-2 flex items-center justify-between gap-3">
          <StatusDot channel={channel} />
          <Toggle
            label={`${label} on`}
            checked={channel.enabled}
            disabled={busy}
            onChange={(v) => void update({ enabled: v })}
          />
        </div>
      )}

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

          {endpoint && (
            <div className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[12.5px]">
              <p className="text-fg-dim">
                {kind === 'whatsapp'
                  ? 'Callback URL for WhatsApp › Configuration in your Meta app:'
                  : "Messaging endpoint for the bot's Configuration page in Azure:"}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-3">
                <span className="select-all break-all font-mono text-[12px]" aria-label="Messaging endpoint">{endpoint}</span>
                <button type="button" onClick={copyEndpoint} className="text-accent-ink hover:underline">
                  {copiedUrl ? 'Copied' : 'Copy'}
                </button>
              </div>
              {channel.verify_token && (
                <p className="mt-1.5 text-fg-dim">
                  Verify token: <span className="select-all break-all font-mono text-[12px] text-fg" aria-label="Verify token">{channel.verify_token}</span>
                </p>
              )}
              {!endpoint.startsWith('https://') && (
                <p className="mt-1.5 text-[11.5px] text-danger-ink">
                  {label} only calls public https addresses. Open Cognitrix at its public address to get the right one.
                </p>
              )}
            </div>
          )}

          {channel.linked ? (
            <p className="text-[12.5px] text-fg-dim">
              Linked to {channel.peer_name ? <span className="text-fg">{channel.peer_name}</span> : 'your chat'}.{' '}
              {channel.mentions
                ? 'It answers that chat, and you when you @mention it in a channel it was added to.'
                : 'Only that chat is answered.'}
            </p>
          ) : channel.link_code ? (
            <div className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[12.5px]">
              <p className="text-fg-dim">
                {kind === 'telegram'
                  ? 'Open the bot and send this code'
                  : kind === 'discord'
                    ? 'Add the bot to a server you are in, then send it this code in a direct message'
                    : kind === 'teams'
                      ? 'Open a direct chat with the bot in Teams and send this code'
                    : kind === 'whatsapp'
                      ? "After saving the callback URL in Meta, message the bot's number on WhatsApp with this code"
                      : 'Open the app in Slack (Apps › Cognitrix › Messages) and send this code'}:
              </p>
              <div className="mt-1.5 flex flex-wrap items-center gap-3">
                <span className="select-all font-mono text-[18px] tracking-[0.18em]" aria-label="Link code">{channel.link_code}</span>
                {channel.link_url && (
                  <a href={channel.link_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                    {kind === 'discord' ? `Add ${channel.bot_name} to a server` : kind === 'whatsapp' ? `Message ${channel.bot_name} on WhatsApp` : `Open ${channel.bot_name} in Telegram`}
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
                <div className="text-[12px] text-fg-dim">
                  Messages agents send with Notify Me.
                  {kind === 'whatsapp' && ' WhatsApp only delivers them within 24 hours of your last message to the bot; later ones stay in your Inbox.'}
                </div>
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
            {channel.mentions && (
              <div className="flex items-center justify-between gap-4 py-2">
                <div>
                  <div className="text-[13px]">Answer other people in channels</div>
                  <div className="text-[12px] leading-snug text-fg-dim">
                    Off: only your own @mentions are answered. On: anyone in a channel the bot was added to can ask.
                    For them the agent can only look things up in its folders, knowledge and the web, on your model key.
                    It can't change anything or use your connected apps, memory, chats or sign-ins.
                  </div>
                </div>
                <Toggle label={`${label} answer other people`} checked={!!channel.answer_others} disabled={busy}
                  onChange={(v) => void update({ answer_others: v })} />
              </div>
            )}
          </div>
          {kind === 'slack' && (
            <p className="text-[12px] leading-snug text-fg-dim">
              Connected before channel mentions existed? In your Slack app add the <span className="font-mono">app_mentions:read</span> scope
              and the <span className="font-mono">app_mention</span> bot event, then reinstall it.
            </p>
          )}

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
              {kind === 'teams' || kind === 'whatsapp' ? 'Change credentials' : 'Change token'}
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

const BLURB: Record<ChannelKind, string> = {
  telegram: 'Chat with an agent from your own Telegram bot',
  slack: 'Chat in Slack DMs, @mention it in channels, approve with buttons',
  discord: 'Chat in Discord DMs and approve tasks with buttons',
  teams: 'Chat in Teams and @mention it in channels (needs a public server)',
  whatsapp: 'Chat with an agent from WhatsApp (needs a public server)',
};

function summary(channel: ChatChannel): string {
  if (!channel.configured) return BLURB[channel.kind];
  if (!channel.enabled) return `${channel.bot_name || LABEL[channel.kind]} · off`;
  if (channel.status === 'error') return 'Needs attention';
  if (!channel.linked) return 'Waiting for the link code';
  return [channel.bot_name, channel.peer_name && `linked to ${channel.peer_name}`].filter(Boolean).join(' · ');
}

/** One compact row per chat app. Setup and options open underneath. */
function ChannelRow({
  channel, agents, manifest, onChange,
}: {
  channel: ChatChannel;
  agents: AgentLite[];
  manifest: Record<string, unknown>;
  onChange: (next: ChatChannel | null) => void;
}) {
  const label = LABEL[channel.kind];
  const needsAttention = channel.configured && channel.enabled && (channel.status === 'error' || !channel.linked);
  const [open, setOpen] = useState(needsAttention);
  const panelId = `channel-${channel.kind}`;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3.5 py-3">
        <span aria-hidden="true" className="grid h-7 w-7 flex-none place-items-center rounded-[7px] bg-panel-2 font-mono text-[11px] font-medium">
          {channel.kind === 'telegram' ? 'TG' : channel.kind === 'discord' ? 'DC' : channel.kind === 'teams' ? 'MS' : channel.kind === 'whatsapp' ? 'WA' : 'SL'}
        </span>
        <div className="min-w-0 flex-1 basis-[160px]">
          <div className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium">
            {label}
            {channel.configured && <StatusDot channel={channel} />}
          </div>
          <div className="mt-0.5 text-[12px] leading-snug text-fg-dim">{summary(channel)}</div>
        </div>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
          className={cn(
            'inline-flex h-9 flex-none items-center rounded-lg px-3 text-[12.5px] md:h-8',
            open ? 'text-fg-dim hover:bg-panel-2 hover:text-fg'
              : channel.configured ? 'border border-line hover:bg-panel-2' : 'bg-accent font-medium text-[#0b0c0e]',
          )}
        >
          {open ? 'Close' : channel.configured ? 'Manage' : 'Set up'}
          <span className="sr-only"> {label}</span>
        </button>
      </div>
      {open && (
        <div id={panelId} className="border-t border-line px-3.5 pb-3 pt-3">
          <ChannelPanel
            channel={channel}
            agents={agents}
            manifest={manifest}
            onChange={(next) => {
              onChange(next);
              if (next === null) setOpen(false);
            }}
          />
        </div>
      )}
    </div>
  );
}

/** Settings › Chat apps: talk to agents from Telegram, Slack, Discord,
 *  Microsoft Teams or WhatsApp and get Inbox alerts and approvals there. All but
 *  Teams and WhatsApp connect out from the engine, so the desktop app needs no public address. */
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
      {!list.available && (
        <p className="rounded-[10px] border border-line bg-bg px-3 py-2 text-[12.5px] text-fg-dim">
          Chat apps are turned off on this engine (<span className="font-mono">COGNITRIX_CHANNELS=0</span>).
        </p>
      )}
      <div className="divide-y divide-line rounded-[12px] border border-line bg-panel">
        {list.channels.map((channel) => (
          <ChannelRow
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
