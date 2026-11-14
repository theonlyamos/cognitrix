import { api, API_BASE } from '@/lib/api';

/** One agent's desktop inside the person's sandboxed computer. */
export interface SandboxDesktop {
  /** Desktop name: the agent's id, made safe for file and URL use. */
  name: string;
  display: string;
  /** Who has taken over this desktop, or null while the agent has it. */
  held_by: string | null;
  running: boolean;
}

export interface SandboxStatus {
  /** The agents' sandboxed computer is switched on for this server. */
  enabled: boolean;
  running: boolean;
  desktops: SandboxDesktop[];
  /** A newer version of the computer has been downloaded; restart to use it. */
  update_available?: boolean;
  error?: string;
}

export async function sandboxStatus(): Promise<SandboxStatus> {
  return (await api.get<SandboxStatus>('/computer/sandbox')).data;
}

/** A one-time link to a desktop's live screen, and its view-only password. */
export async function openViewer(desktop: string): Promise<{ url: string; password: string }> {
  const { data } = await api.post<{ path: string; password: string }>(`/computer/sandbox/desktops/${encodeURIComponent(desktop)}/viewer`);
  return { url: websocketUrl(data.path), password: data.password };
}

/** Take over: the agent pauses; the returned password lets the screen take input. */
export async function takeOver(desktop: string): Promise<string> {
  const { data } = await api.post<{ password: string }>(`/computer/sandbox/desktops/${encodeURIComponent(desktop)}/hold`, {});
  return data.password;
}

export async function handBack(desktop: string): Promise<void> {
  await api.post(`/computer/sandbox/desktops/${encodeURIComponent(desktop)}/release`);
}

/** Restart the computer on the newer version. Open windows close; files and logins stay. */
export async function updateSandbox(): Promise<void> {
  await api.post('/computer/sandbox/update');
}

/** Throw the computer away, files and logins included. */
export async function resetSandbox(): Promise<void> {
  await api.post('/computer/sandbox/reset');
}

/** The desktop name the engine uses for an agent (see cognitrix/computer/sandbox desktop_name). */
export function desktopName(agentId: string | null | undefined): string {
  const text = String(agentId || 'main').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^[-_]+|[-_]+$/g, '').slice(0, 48) || 'main';
  return /^[a-z0-9][a-z0-9_-]{0,47}$/.test(text) ? text : `d-${text.slice(0, 46)}`;
}

export function websocketUrl(path: string): string {
  const base = API_BASE.startsWith('http') ? new URL(API_BASE) : new URL(API_BASE, window.location.origin);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${base.toString().replace(/\/$/, '')}/${path.replace(/^\//, '')}`;
}

export interface SandboxSettings {
  enabled: boolean;
  /** COGNITRIX_COMPUTER_SANDBOX is set on the server, so it decides. */
  from_env: boolean;
  image: string;
  docker: { installed: boolean; running: boolean; image: boolean };
}

export async function getSandboxSettings(): Promise<SandboxSettings> {
  return (await api.get<SandboxSettings>('/computer/sandbox/settings')).data;
}

export async function setSandboxEnabled(enabled: boolean): Promise<SandboxSettings> {
  return (await api.put<SandboxSettings>('/computer/sandbox/settings', { enabled })).data;
}
