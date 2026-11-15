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
import { tr } from '@/lib/i18n';

interface AgentLite { id: string; name: string }

const LABEL: Record<ChannelKind, string> = { telegram: 'Telegram', slack: 'Slack', discord: 'Discord', teams: 'Microsoft Teams', whatsapp: 'WhatsApp', sms: 'SMS' };

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
          {tr('ui.chatChannelsCard.inTelegramOpen')}{' '}
          <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">@BotFather</a>{' '}{tr('ui.chatChannelsCard.andSend')}{' '}<span className="font-mono">/newbot</span>.
        </li>
        <li>{tr('ui.chatChannelsCard.pickANameAndUsername')}{' '}<span className="font-mono">123456:ABC…</span>.</li>
        <li>{tr('ui.chatChannelsCard.pasteTheTokenBelowThen')}</li>
      </ol>
    );
  }
  if (kind === 'discord') {
    return (
      <ol className="list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-fg-dim">
        <li>
          {tr('ui.chatChannelsCard.openThe')}{' '}
          <a href="https://discord.com/developers/applications" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{tr('ui.chatChannelsCard.discordDeveloperPortal')}</a>{tr('ui.chatChannelsCard.chooseNewApplicationThenOpen')}
        </li>
        <li>{tr('ui.chatChannelsCard.pressResetTokenAndCopy')}</li>
        <li>{tr('ui.chatChannelsCard.pasteTheTokenBelowCognitrix')}</li>
        <li>{tr('ui.chatChannelsCard.sendTheBotADirect')}</li>
      </ol>
    );
  }
  if (kind === 'whatsapp') {
    return (
      <div className="space-y-2">
        <p className="rounded-[10px] border border-line bg-bg px-3 py-2 text-[12.5px] leading-relaxed text-fg-dim">
          {tr('ui.chatChannelsCard.whatsappSendsMessagesToThe')}
          <span className="font-mono"> https://</span>{' '}{tr('ui.chatChannelsCard.addressItDoesNotWork')}
        </p>
        <ol className="list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-fg-dim">
          <li>
            {tr('ui.chatChannelsCard.on')}{' '}
            <a href="https://developers.facebook.com/apps" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">developers.facebook.com</a>{tr('ui.chatChannelsCard.createABusinessAppAnd')}
          </li>
          <li>{tr('ui.chatChannelsCard.fromWhatsappApiSetupCopy')}</li>
          <li>{tr('ui.chatChannelsCard.fromAppSettingsBasicCopy')}</li>
          <li>{tr('ui.chatChannelsCard.cognitrixThenShowsACallback')}{' '}<span className="font-mono">{tr('ui.chatChannelsCard.messages')}</span>{' '}{tr('ui.chatChannelsCard.field')}</li>
          <li>{tr('ui.chatChannelsCard.sendTheBotSNumber')}</li>
        </ol>
      </div>
    );
  }
  if (kind === 'sms') {
    return (
      <div className="space-y-2">
        <p className="rounded-[10px] border border-line bg-bg px-3 py-2 text-[12.5px] leading-relaxed text-fg-dim">
          {tr('ui.chatChannelsCard.twilioSendsTextsToCognitrix')}
          <span className="font-mono"> https://</span>{' '}{tr('ui.chatChannelsCard.addressEachTextInAnd')}
        </p>
        <ol className="list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-fg-dim">
          <li>
            {tr('ui.chatChannelsCard.on')}{' '}
            <a href="https://console.twilio.com" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">console.twilio.com</a>{tr('ui.chatChannelsCard.buyAPhoneNumberThat')}
          </li>
          <li>{tr('ui.chatChannelsCard.copyTheAccountSidAnd')}</li>
          <li>{tr('ui.chatChannelsCard.cognitrixThenShowsAWebhook')}</li>
          <li>{tr('ui.chatChannelsCard.textTheNumberTheCode')}</li>
        </ol>
      </div>
    );
  }
  if (kind === 'teams') {
    return (
      <div className="space-y-2">
        <p className="rounded-[10px] border border-line bg-bg px-3 py-2 text-[12.5px] leading-relaxed text-fg-dim">
          {tr('ui.chatChannelsCard.teamsSendsMessagesToThe')}
          <span className="font-mono"> https://</span>{' '}{tr('ui.chatChannelsCard.addressItDoesNotWork')}
        </p>
        <ol className="list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-fg-dim">
          <li>
            {tr('ui.chatChannelsCard.inThe')}{' '}
            <a href="https://portal.azure.com/#create/Microsoft.AzureBot" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{tr('ui.chatChannelsCard.azurePortal')}</a>{tr('ui.chatChannelsCard.createAnAzureBotWith')}
          </li>
          <li>{tr('ui.chatChannelsCard.copyTheMicrosoftAppId')}</li>
          <li>{tr('ui.chatChannelsCard.pasteAllThreeBelowCognitrix')}</li>
          <li>{tr('ui.chatChannelsCard.addTheBotToTeams')}</li>
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
        {tr('ui.chatChannelsCard.copyThe')}{' '}
        <button type="button" onClick={copy} className="text-accent-ink hover:underline">
          {copied ? tr('ui.chatChannelsCard.manifestCopied') : tr('ui.chatChannelsCard.appManifest')}
        </button>
        {tr('ui.chatChannelsCard.thenOn')}{' '}
        <a href="https://api.slack.com/apps?new_app=1" target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">api.slack.com/apps</a>{' '}{tr('ui.chatChannelsCard.chooseCreateNewAppFrom')}
      </li>
      <li>{tr('ui.chatChannelsCard.installTheAppToYour')}<span className="font-mono">xoxb-…</span>).</li>
      <li>
        {tr('ui.chatChannelsCard.underBasicInformationAppLevel')}{' '}<span className="font-mono">connections:write</span>{' '}{tr('ui.chatChannelsCard.scope')}<span className="font-mono">xapp-…</span>).
      </li>
      <li>{tr('ui.chatChannelsCard.pasteBothBelowThenOpen')}</li>
      <li>{tr('ui.chatChannelsCard.toUseItInA')}</li>
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
  const [phone, setPhone] = useState(channel.bot_name && kind === 'sms' ? channel.bot_name : '');
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
      setError(errorMessage(e, tr('ui.chatChannelsCard.couldNotUpdateLabel', { label })));
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
    if (kind === 'sms') {
      body.app_id = appId.trim();
      body.phone_number = phone.trim();
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
    || (kind === 'whatsapp' && (!appId.trim() || (!appSecret.trim() && !channel.configured)))
    || (kind === 'sms' && (!appId.trim() || !phone.trim()));
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
          <Field label={tr('ui.chatChannelsCard.microsoftAppId')}>
            <Input autoComplete="off" aria-label={tr('ui.chatChannelsCard.teamsAppId')} placeholder="00000000-0000-0000-0000-000000000000"
              value={appId} onChange={(e) => setAppId(e.target.value)} disabled={busy} />
          </Field>
          <Field label={tr('ui.chatChannelsCard.tenantId')}>
            <Input autoComplete="off" aria-label={tr('ui.chatChannelsCard.teamsTenantId')} placeholder="00000000-0000-0000-0000-000000000000"
              value={tenantId} onChange={(e) => setTenantId(e.target.value)} disabled={busy} />
          </Field>
        </>
      )}
      {kind === 'sms' && (
        <>
          <Field label={tr('ui.chatChannelsCard.accountSid')}>
            <Input autoComplete="off" aria-label={tr('ui.chatChannelsCard.twilioAccountSid')} placeholder={tr('ui.chatChannelsCard.ac')}
              value={appId} onChange={(e) => setAppId(e.target.value)} disabled={busy} />
          </Field>
          <Field label={tr('ui.chatChannelsCard.twilioNumber')} hint={tr('ui.chatChannelsCard.withCountryCode')}>
            <Input autoComplete="off" inputMode="tel" aria-label={tr('ui.chatChannelsCard.twilioPhoneNumber')} placeholder="+15550102000"
              value={phone} onChange={(e) => setPhone(e.target.value)} disabled={busy} />
          </Field>
        </>
      )}
      {kind === 'whatsapp' && (
        <Field label={tr('ui.chatChannelsCard.phoneNumberId')}>
          <Input autoComplete="off" inputMode="numeric" aria-label={tr('ui.chatChannelsCard.whatsappPhoneNumberId')} placeholder="106540352242922"
            value={appId} onChange={(e) => setAppId(e.target.value)} disabled={busy} />
        </Field>
      )}
      <Field label={kind === 'slack' ? tr('ui.chatChannelsCard.botTokenXoxb') : kind === 'teams' ? tr('ui.chatChannelsCard.clientSecret') : kind === 'whatsapp' ? tr('ui.chatChannelsCard.accessToken') : kind === 'sms' ? tr('ui.chatChannelsCard.authToken') : tr('ui.chatChannelsCard.botToken')}>
        <Input
          type="password"
          autoComplete="off"
          aria-label={kind === 'teams' ? tr('ui.chatChannelsCard.teamsClientSecret') : kind === 'whatsapp' ? tr('ui.chatChannelsCard.whatsappAccessToken') : kind === 'sms' ? tr('ui.chatChannelsCard.twilioAuthToken') : tr('ui.chatChannelsCard.labelBotToken', { label })}
          placeholder={channel.bot_token_hint ? tr('ui.chatChannelsCard.savedBotTokenHint', { bot_token_hint: channel.bot_token_hint }) : kind === 'slack' ? 'xoxb-…' : kind === 'teams' ? tr('ui.chatChannelsCard.clientSecretValue') : kind === 'whatsapp' ? 'EAAG…' : kind === 'sms' ? tr('ui.chatChannelsCard.fromTheTwilioConsole') : '123456:ABC…'}
          value={botToken}
          onChange={(e) => setBotToken(e.target.value)}
          disabled={busy}
        />
      </Field>
      {kind === 'slack' && (
        <Field label={tr('ui.chatChannelsCard.appLevelTokenXapp')}>
          <Input
            type="password"
            autoComplete="off"
            aria-label={tr('ui.chatChannelsCard.slackAppLevelToken')}
            placeholder="xapp-…"
            value={appToken}
            onChange={(e) => setAppToken(e.target.value)}
            disabled={busy}
          />
        </Field>
      )}
      {kind === 'whatsapp' && (
        <Field label={tr('ui.chatChannelsCard.appSecret')}>
          <Input type="password" autoComplete="off" aria-label={tr('ui.chatChannelsCard.whatsappAppSecret')}
            placeholder={channel.configured ? tr('ui.chatChannelsCard.savedLeaveEmptyToKeep') : tr('ui.chatChannelsCard.fromAppSettingsBasic')}
            value={appSecret} onChange={(e) => setAppSecret(e.target.value)} disabled={busy} />
        </Field>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy || missing}
          className="h-10 rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-foreground disabled:opacity-60"
        >
          {busy ? tr('ui.chatChannelsCard.checking') : channel.configured ? tr('ui.chatChannelsCard.saveTokens') : tr('ui.chatChannelsCard.connectLabel', { label })}
        </button>
        {channel.configured && (
          <button type="button" onClick={() => setEditing(false)} className="h-10 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
            {tr('ui.chatChannelsCard.cancel')}
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
            label={tr('ui.chatChannelsCard.labelOn', { label })}
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
                  ? tr('ui.chatChannelsCard.callbackUrlForWhatsappConfiguration')
                  : kind === 'sms'
                  ? tr('ui.chatChannelsCard.webhookUrlForYourTwilio')
                  : tr('ui.chatChannelsCard.messagingEndpointForTheBot')}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-3">
                <span className="select-all break-all font-mono text-[12px]" aria-label={tr('ui.chatChannelsCard.messagingEndpoint')}>{endpoint}</span>
                <button type="button" onClick={copyEndpoint} className="text-accent-ink hover:underline">
                  {copiedUrl ? tr('ui.chatChannelsCard.copied') : tr('ui.chatChannelsCard.copy')}
                </button>
              </div>
              {channel.verify_token && (
                <p className="mt-1.5 text-fg-dim">
                  {tr('ui.chatChannelsCard.verifyToken_942c')}{' '}<span className="select-all break-all font-mono text-[12px] text-fg" aria-label={tr('ui.chatChannelsCard.verifyToken')}>{channel.verify_token}</span>
                </p>
              )}
              {!endpoint.startsWith('https://') && (
                <p className="mt-1.5 text-[11.5px] text-danger-ink">
                  {tr('ui.chatChannelsCard.labelOnlyCallsPublicHttps', { label })}
                </p>
              )}
            </div>
          )}

          {channel.linked ? (
            <p className="text-[12.5px] text-fg-dim">
              {tr('ui.chatChannelsCard.linkedTo')}{' '}{channel.peer_name ? <span className="text-fg">{channel.peer_name}</span> : tr('ui.chatChannelsCard.yourChat')}.{' '}
              {channel.mentions
                ? tr('ui.chatChannelsCard.itAnswersThatChatAnd')
                : tr('ui.chatChannelsCard.onlyThatChatIsAnswered')}
            </p>
          ) : channel.link_code ? (
            <div className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[12.5px]">
              <p className="text-fg-dim">
                {kind === 'telegram'
                  ? tr('ui.chatChannelsCard.openTheBotAndSend')
                  : kind === 'discord'
                    ? tr('ui.chatChannelsCard.addTheBotToA')
                    : kind === 'teams'
                      ? tr('ui.chatChannelsCard.openADirectChatWith')
                    : kind === 'whatsapp'
                      ? tr('ui.chatChannelsCard.afterSavingTheCallbackUrl')
                    : kind === 'sms'
                      ? tr('ui.chatChannelsCard.afterSavingTheWebhookUrl', { bot_name: channel.bot_name })
                      : tr('ui.chatChannelsCard.openTheAppInSlack')}:
              </p>
              <div className="mt-1.5 flex flex-wrap items-center gap-3">
                <span className="select-all font-mono text-[18px] tracking-[0.18em]" aria-label={tr('ui.chatChannelsCard.linkCode')}>{channel.link_code}</span>
                {channel.link_url && (
                  <a href={channel.link_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                    {kind === 'discord' ? tr('ui.chatChannelsCard.addBotNameToA', { bot_name: channel.bot_name }) : kind === 'whatsapp' ? tr('ui.chatChannelsCard.messageBotNameOnWhatsapp', { bot_name: channel.bot_name }) : kind === 'sms' ? tr('ui.chatChannelsCard.textBotName', { bot_name: channel.bot_name }) : tr('ui.chatChannelsCard.openBotNameInTelegram', { bot_name: channel.bot_name })}
                  </a>
                )}
              </div>
              <p className="mt-1.5 text-[11.5px] text-fg-dim">{tr('ui.chatChannelsCard.theCodeWorksFor30')}</p>
            </div>
          ) : (
            <p className="text-[12.5px] text-fg-dim">
              {tr('ui.chatChannelsCard.theLinkCodeExpired')}{' '}
              <button type="button" onClick={() => void run(() => relinkChannel(kind))} className="text-accent-ink hover:underline">{tr('ui.chatChannelsCard.getANewCode')}</button>
            </p>
          )}

          <Field label={tr('ui.chatChannelsCard.agent')} hint={tr('ui.chatChannelsCard.newConversationsUseItChange')}>
            <Select
              value={channel.agent_id}
              disabled={busy}
              onChange={(e) => void update({ agent_id: e.target.value }, tr('ui.chatChannelsCard.agentSaved'))}
              aria-label={tr('ui.chatChannelsCard.labelAgent', { label })}
            >
              <option value="">{tr('ui.chatChannelsCard.yourFirstAgent')}</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          </Field>

          <div className="divide-y divide-line">
            <div className="flex items-center justify-between gap-4 py-2">
              <div>
                <div className="text-[13px]">{tr('ui.chatChannelsCard.agentAlerts')}</div>
                <div className="text-[12px] text-fg-dim">
                  {tr('ui.chatChannelsCard.messagesAgentsSendWithNotify', { value: kind === 'whatsapp' && tr('ui.chatChannelsCard.whatsappOnlyDeliversThemWithin') })}
                </div>
              </div>
              <Toggle label={tr('ui.chatChannelsCard.labelAgentAlerts', { label })} checked={channel.send_alerts} disabled={busy}
                onChange={(v) => void update({ send_alerts: v })} />
            </div>
            <div className="flex items-center justify-between gap-4 py-2">
              <div>
                <div className="text-[13px]">{tr('ui.chatChannelsCard.taskApprovals')}</div>
                <div className="text-[12px] text-fg-dim">{tr('ui.chatChannelsCard.approveOrDenyWithButtons')}</div>
              </div>
              <Toggle label={tr('ui.chatChannelsCard.labelTaskApprovals', { label })} checked={channel.send_approvals} disabled={busy}
                onChange={(v) => void update({ send_approvals: v })} />
            </div>
            {channel.mentions && (
              <div className="flex items-center justify-between gap-4 py-2">
                <div>
                  <div className="text-[13px]">{tr('ui.chatChannelsCard.answerOtherPeopleInChannels')}</div>
                  <div className="text-[12px] leading-snug text-fg-dim">
                    {tr('ui.chatChannelsCard.offOnlyYourOwnMentions')}
                  </div>
                </div>
                <Toggle label={tr('ui.chatChannelsCard.labelAnswerOtherPeople', { label })} checked={!!channel.answer_others} disabled={busy}
                  onChange={(v) => void update({ answer_others: v })} />
              </div>
            )}
          </div>
          {kind === 'slack' && (
            <p className="text-[12px] leading-snug text-fg-dim">
              {tr('ui.chatChannelsCard.connectedBeforeChannelMentionsExisted')}{' '}<span className="font-mono">app_mentions:read</span>{' '}{tr('ui.chatChannelsCard.scopeAndThe')}{' '}<span className="font-mono">app_mention</span>{' '}{tr('ui.chatChannelsCard.botEventThenReinstallIt')}
            </p>
          )}

          <div className="flex flex-wrap gap-2 text-[12.5px]">
            {channel.linked && (
              <button type="button" disabled={busy || !channel.connected}
                onClick={() => void run(() => testChannel(kind), tr('ui.chatChannelsCard.testMessageSent'))}
                className="h-9 rounded-lg border border-line px-3 hover:bg-panel-2 disabled:opacity-60">
                {tr('ui.chatChannelsCard.sendTestMessage')}
              </button>
            )}
            {channel.linked && (
              <button type="button" disabled={busy} onClick={() => void run(() => relinkChannel(kind))}
                className="h-9 rounded-lg border border-line px-3 hover:bg-panel-2 disabled:opacity-60">
                {tr('ui.chatChannelsCard.linkADifferentChat')}
              </button>
            )}
            <button type="button" disabled={busy} onClick={() => setEditing(true)}
              className="h-9 rounded-lg border border-line px-3 hover:bg-panel-2 disabled:opacity-60">
              {kind === 'teams' || kind === 'whatsapp' || kind === 'sms' ? tr('ui.chatChannelsCard.changeCredentials') : tr('ui.chatChannelsCard.changeToken')}
            </button>
            <button type="button" disabled={busy}
              onClick={() => void run(async () => { await deleteChannel(kind); return null; }, tr('ui.chatChannelsCard.labelDisconnected', { label }))}
              className="h-9 rounded-lg px-3 text-danger-ink hover:underline disabled:opacity-60">
              {tr('ui.chatChannelsCard.disconnect')}
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
  sms: 'Text an agent from your phone with a Twilio number (needs a public server)',
};

function summary(channel: ChatChannel): string {
  if (!channel.configured) return BLURB[channel.kind];
  if (!channel.enabled) return tr('ui.chatChannelsCard.botNameOff', { bot_name: channel.bot_name || LABEL[channel.kind] });
  if (channel.status === 'error') return tr('ui.chatChannelsCard.needsAttention');
  if (!channel.linked) return tr('ui.chatChannelsCard.waitingForTheLinkCode');
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
          {channel.kind === 'telegram' ? 'TG' : channel.kind === 'discord' ? 'DC' : channel.kind === 'teams' ? 'MS' : channel.kind === 'whatsapp' ? 'WA' : channel.kind === 'sms' ? 'SM' : 'SL'}
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
              : channel.configured ? 'border border-line hover:bg-panel-2' : 'bg-accent font-medium text-accent-foreground',
          )}
        >
          {open ? tr('ui.chatChannelsCard.close') : channel.configured ? tr('ui.chatChannelsCard.manage') : tr('ui.chatChannelsCard.setUp')}
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
      .catch((e) => setLoadError(errorMessage(e, tr('ui.chatChannelsCard.couldNotLoadChatApps'))));

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
  if (!list) return <p className="text-[13px] text-fg-dim">{tr('ui.chatChannelsCard.loading')}</p>;

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
          {tr('ui.chatChannelsCard.chatAppsAreTurnedOff')}<span className="font-mono">COGNITRIX_CHANNELS=0</span>).
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
