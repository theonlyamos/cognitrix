import { api } from '@/lib/api';

export type ChannelKind = 'telegram' | 'slack' | 'discord' | 'teams' | 'whatsapp';

export interface ChatChannel {
  kind: ChannelKind;
  /** A bot token is saved. Tokens themselves never come back. */
  configured: boolean;
  enabled: boolean;
  /** A chat sent the link code; only that chat is answered. */
  linked: boolean;
  bot_token_hint: string | null;
  app_token_set: boolean;
  bot_name: string;
  /** '' means the person's first agent. */
  agent_id: string;
  send_alerts: boolean;
  send_approvals: boolean;
  peer_name: string;
  linked_at: string;
  /** One-time code to send to the bot; '' once linked or expired. */
  link_code: string;
  link_code_expires_at?: string;
  /** Telegram: opens the bot with the code filled in. */
  link_url: string;
  status: '' | 'connecting' | 'connected' | 'error';
  last_error: string;
  connected: boolean;
  /** Slack and Teams: the bot also answers @mentions in shared channels. */
  mentions?: boolean;
  /** Also answer people other than the linked person there (read-only). */
  answer_others?: boolean;
  /** Teams: the bot's Microsoft App ID and organisation. */
  app_id?: string;
  tenant_id?: string;
  /** Teams and WhatsApp: path the app calls, after the server's public address. */
  webhook_path?: string;
  /** WhatsApp: the "Verify token" for Meta's webhook form. */
  verify_token?: string;
  /** The app calls the engine, so it needs a public https address. */
  needs_public_url?: boolean;
}

export interface ChannelList {
  /** False when the engine runs with COGNITRIX_CHANNELS=0. */
  available: boolean;
  channels: ChatChannel[];
  slack_manifest: Record<string, unknown>;
}

export interface ChannelUpdate {
  bot_token?: string;
  app_token?: string;
  agent_id?: string;
  enabled?: boolean;
  send_alerts?: boolean;
  send_approvals?: boolean;
  answer_others?: boolean;
  app_id?: string;
  tenant_id?: string;
  app_secret?: string;
}

export async function listChannels(): Promise<ChannelList> {
  return (await api.get<ChannelList>('/channels')).data;
}

export async function saveChannel(kind: ChannelKind, update: ChannelUpdate): Promise<ChatChannel> {
  return (await api.put<ChatChannel>(`/channels/${kind}`, update)).data;
}

export async function relinkChannel(kind: ChannelKind): Promise<ChatChannel> {
  return (await api.post<ChatChannel>(`/channels/${kind}/relink`)).data;
}

export async function testChannel(kind: ChannelKind): Promise<void> {
  await api.post(`/channels/${kind}/test`);
}

export async function deleteChannel(kind: ChannelKind): Promise<void> {
  await api.delete(`/channels/${kind}`);
}
