import { api } from '@/lib/api';

export type ChannelKind = 'telegram' | 'slack';

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
