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
  /** Where computers run: this server's Docker, or Fly Machines. */
  host?: 'docker' | 'fly';
  /** Hosted: the computer exists but is stopped while nobody uses it. */
  asleep?: boolean;
  error?: string;
  /** Hosted: the computer is a machine on the owner's tailnet (Settings › Tailscale). */
  tailnet?: { name: string; dns: string; online: boolean };
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

/** Start a hosted computer that is asleep. */
export async function wakeSandbox(desktop?: string | null): Promise<void> {
  await api.post('/computer/sandbox/wake', { desktop: desktop ?? null });
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
  host?: 'docker' | 'fly';
  /** Hosted on Fly: the settings the server has (never the token). */
  fly?: { app: string; region: string; configured: boolean; missing: string[]; idle_minutes: number; idle_action: string; size: string };
}

export async function getSandboxSettings(): Promise<SandboxSettings> {
  return (await api.get<SandboxSettings>('/computer/sandbox/settings')).data;
}

export async function setSandboxEnabled(enabled: boolean): Promise<SandboxSettings> {
  return (await api.put<SandboxSettings>('/computer/sandbox/settings', { enabled })).data;
}

/** Run in sandbox for one chat: its shell, files and tests work in the agents' sandboxed computer. */
export interface ChatSandbox {
  /** The chat's own choice; null follows the agent's default. */
  run_in_sandbox: boolean | null;
  agent_default: boolean;
  effective: boolean;
  /** The sandboxed computer is switched on for this server. */
  available: boolean;
}

export async function getChatSandbox(sessionId: string): Promise<ChatSandbox> {
  return (await api.get<ChatSandbox>(`/computer/sandbox/chats/${encodeURIComponent(sessionId)}`)).data;
}

export async function setChatSandbox(sessionId: string, on: boolean | null): Promise<ChatSandbox> {
  return (await api.put<ChatSandbox>(`/computer/sandbox/chats/${encodeURIComponent(sessionId)}`, { run_in_sandbox: on })).data;
}

/** A variable every command in the person's sandbox gets. Values never come back, only a masked preview. */
export interface SandboxEnvVar {
  name: string;
  preview?: string;
  /** New value to save; left out keeps the saved one. */
  value?: string;
  /**
   * The only sites the value is sent to. With sites, commands get a placeholder and
   * Cognitrix fills the real value in only on requests to them. Left out keeps the saved sites.
   */
  hosts?: string[];
}

export async function getSandboxEnv(): Promise<SandboxEnvVar[]> {
  return (await api.get<{ variables: SandboxEnvVar[] }>('/computer/sandbox/env')).data.variables;
}

export async function saveSandboxEnv(variables: SandboxEnvVar[]): Promise<SandboxEnvVar[]> {
  const body = {
    variables: variables.map((v) => {
      const out: { name: string; value?: string; hosts?: string[] } = { name: v.name };
      if (v.value !== undefined) out.value = v.value;
      if (v.hosts !== undefined) out.hosts = v.hosts;
      return out;
    }),
  };
  return (await api.put<{ variables: SandboxEnvVar[] }>('/computer/sandbox/env', body)).data.variables;
}

/** A saved copy of the whole computer, or of one agent's setup on it. */
export interface SandboxSnapshot {
  id: string;
  name: string;
  kind: 'computer' | 'agent';
  /** Desktop name of the agent an agent setup was saved from. */
  agent: string | null;
  size: number;
  /** Seconds since the epoch. */
  created_at: number;
}

export async function listSnapshots(): Promise<{ snapshots: SandboxSnapshot[]; max: number }> {
  return (await api.get<{ snapshots: SandboxSnapshot[]; max: number }>('/computer/sandbox/snapshots')).data;
}

/** Save the whole computer, or only one agent's setup (its desktop name). */
export async function createSnapshot(name: string, agent?: string | null): Promise<SandboxSnapshot> {
  return (await api.post<SandboxSnapshot>('/computer/sandbox/snapshots', { name, agent: agent || null }, { timeout: 0 })).data;
}

/** Put a snapshot back. An agent setup goes to `agent` (default: the agent it came from). */
export async function restoreSnapshot(id: string, agent?: string | null): Promise<void> {
  await api.post(`/computer/sandbox/snapshots/${encodeURIComponent(id)}/restore`, { agent: agent || null }, { timeout: 0 });
}

export async function deleteSnapshot(id: string): Promise<void> {
  await api.delete(`/computer/sandbox/snapshots/${encodeURIComponent(id)}`);
}

/** Give one agent a copy of another's folder, browser sign-ins and projects on the computer. */
export async function copyAgentSetup(from: string, to: string): Promise<void> {
  await api.post('/computer/sandbox/copy-agent', { from, to }, { timeout: 0 });
}

/** Answer an agent's Ask For Sign-in card: the value goes straight to Saved sign-ins. */
export async function answerSignInRequest(requestId: string, values: { value?: string; username?: string; password?: string }): Promise<void> {
  await api.post(`/credentials/requests/${encodeURIComponent(requestId)}`, values);
}

/** Teach a task: one recorded step of what the person did on the desktop. */
export interface TaughtStep {
  kind: 'click' | 'double_click' | 'right_click' | 'middle_click' | 'type' | 'key' | 'scroll' | 'window_opened';
  app?: string;
  window?: string;
  target?: string;
  text?: string;
  keys?: string;
  direction?: string;
  amount?: number;
  /** Where in the window, 0-1000 across and down. */
  at?: [number, number];
  /** Still being typed. */
  typing?: boolean;
}

export interface TeachRecording {
  steps: TaughtStep[];
  /** The steps as numbered lines. */
  text: string;
  recording?: boolean;
  truncated?: boolean;
  error?: string;
}

/** Take over and start recording; returns the password that lets the screen take input. */
export async function startTeaching(desktop: string): Promise<string> {
  const { data } = await api.post<{ password: string }>(`/computer/sandbox/desktops/${encodeURIComponent(desktop)}/teach/start`, {});
  return data.password;
}

export async function teachingStatus(desktop: string): Promise<TeachRecording> {
  return (await api.get<TeachRecording>(`/computer/sandbox/desktops/${encodeURIComponent(desktop)}/teach`)).data;
}

/** Stop recording and hand the desktop back to the agent. */
export async function stopTeaching(desktop: string): Promise<TeachRecording> {
  return (await api.post<TeachRecording>(`/computer/sandbox/desktops/${encodeURIComponent(desktop)}/teach/stop`, { hand_back: true })).data;
}

export interface TaughtSkill {
  name: string;
  description: string;
  body: string;
  /** The agent whose model wrote it. */
  agent?: string;
}

/** A model writes a skill from the steps. Nothing is saved yet. */
export async function draftTaughtSkill(input: { steps: TaughtStep[]; title: string; note: string; desktop: string; agentId?: string | null }): Promise<TaughtSkill> {
  const { data } = await api.post<TaughtSkill>('/computer/sandbox/teach/draft', {
    steps: input.steps, title: input.title, note: input.note, desktop: input.desktop, agent_id: input.agentId ?? null,
  });
  return data;
}

/** Save it as one of the person's own skills. */
export async function saveTaughtSkill(skill: TaughtSkill): Promise<void> {
  await api.post('/skills/mine', { name: skill.name, description: skill.description, body: skill.body });
}
