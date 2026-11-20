import { api } from '@/lib/api';

/** What a paired PC lets agents do: work with files, run commands. */
export type DevicePower = 'files' | 'commands' | 'apps' | 'tasks';

export interface DeviceFolder {
  path: string;
  writable: boolean;
}

/** One of the person's own PCs, paired with this account. */
export interface PairedDevice {
  id: string;
  name: string;
  platform: string;
  online: boolean;
  paused: boolean;
  folders: DeviceFolder[];
  powers: DevicePower[];
  version?: string;
  paired_at?: string;
  last_seen_at?: string;
}

export interface PairCode {
  code: string;
  expires_in: number;
  /** This server's address, for `cognitrix connect`. */
  server: string;
}

export type PairCodeStatus =
  | { status: 'waiting'; expires_in: number }
  | { status: 'expired' }
  | { status: 'paired'; device_id: string; device?: PairedDevice };

export async function listDevices(): Promise<PairedDevice[]> {
  return (await api.get<{ devices: PairedDevice[] }>('/computer/devices')).data.devices || [];
}

export async function makePairCode(): Promise<PairCode> {
  return (await api.post<PairCode>('/computer/devices/pair-codes')).data;
}

export async function pairCodeStatus(code: string): Promise<PairCodeStatus> {
  return (await api.get<PairCodeStatus>(`/computer/devices/pair-codes/${encodeURIComponent(code)}`)).data;
}

export async function renameDevice(id: string, name: string): Promise<PairedDevice> {
  return (await api.patch<PairedDevice>(`/computer/devices/${encodeURIComponent(id)}`, { name })).data;
}

export async function unpairDevice(id: string): Promise<void> {
  await api.delete(`/computer/devices/${encodeURIComponent(id)}`);
}

export interface ChatComputer {
  /** The PC this chat works on, or null for the agent's usual computer. */
  computer: string | null;
  device: PairedDevice | null;
}

export async function getChatComputer(sessionId: string): Promise<ChatComputer> {
  return (await api.get<ChatComputer>(`/computer/devices/chats/${encodeURIComponent(sessionId)}`)).data;
}

export async function setChatComputer(sessionId: string, computer: string | null): Promise<ChatComputer> {
  return (await api.put<ChatComputer>(`/computer/devices/chats/${encodeURIComponent(sessionId)}`, { computer })).data;
}

export const platformLabel = (platform: string) => {
  const p = (platform || '').toLowerCase();
  if (p.includes('win')) return 'Windows';
  if (p.includes('darwin') || p.includes('mac')) return 'macOS';
  if (p.includes('linux')) return 'Linux';
  return '';
};

// ── Desktop app: link this PC to a web account ──

export interface PcLink {
  linked: boolean;
  server: string;
  account: string;
  device_id: string;
  name: string;
  folders: DeviceFolder[];
  powers: DevicePower[];
  paused: boolean;
  /** Connection to the web account: online, connecting, offline, unpaired. */
  status: string;
  error: string | null;
  connected_since: string | null;
  default_name: string;
  platform: string;
}

export async function getPcLink(): Promise<PcLink> {
  return (await api.get<PcLink>('/computer/link')).data;
}

/** Whose account a code belongs to, before anything is allowed. */
export async function previewPcLink(server: string, code: string): Promise<{ account: string; expires_in: number }> {
  return (await api.post<{ account: string; expires_in: number }>('/computer/link/preview', { server, code })).data;
}

export async function makePcLink(body: { server: string; code: string; name: string; folders: DeviceFolder[]; powers: DevicePower[] }): Promise<PcLink> {
  return (await api.post<PcLink>('/computer/link', body)).data;
}

export async function changePcLink(patch: Partial<Pick<PcLink, 'name' | 'folders' | 'powers' | 'paused'>>): Promise<PcLink> {
  return (await api.patch<PcLink>('/computer/link', patch)).data;
}

export async function removePcLink(): Promise<PcLink> {
  return (await api.delete<PcLink>('/computer/link')).data;
}

/** A fresh picture of the PC's screen (only when the PC allows apps). */
export async function deviceScreen(id: string): Promise<{ image: string; at: number }> {
  return (await api.get<{ image: string; at: number }>(`/computer/devices/${encodeURIComponent(id)}/screen`)).data;
}

// ── Desktop app: hand tasks to the linked web account ──

export interface SentTask {
  id: string;
  title: string;
  status: string;
  result: string;
  /** The task on the web account. */
  url: string;
}

export async function remoteAgents(): Promise<{ id: string; name: string }[]> {
  return (await api.get<{ agents: { id: string; name: string }[] }>('/computer/link/remote/agents')).data.agents || [];
}

export async function sendTaskToWeb(body: { title: string; description: string; steps: string[]; agent_id: string | null }): Promise<{ task_id: string; url: string }> {
  return (await api.post<{ task_id: string; url: string }>('/computer/link/remote/tasks', body)).data;
}

export async function sentTasks(): Promise<SentTask[]> {
  return (await api.get<{ tasks: SentTask[] }>('/computer/link/remote/tasks')).data.tasks || [];
}
