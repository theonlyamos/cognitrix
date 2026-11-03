import { api } from '@/lib/api';
import { desktop, isDesktop } from '@/lib/desktop';

export type AppStatus = 'connected' | 'needs_reauth' | 'signed_out';

export interface AppConnection {
  server_id: string;
  name: string;
  url: string | null;
  auth_type: 'oauth' | 'header' | 'none';
  status: AppStatus;
  /** Who it's signed in as: an email address or a GitHub login. */
  account: string;
  enabled: boolean;
}

export interface CatalogApp {
  id: string;
  name: string;
  description: string;
  category: string;
  auth: 'provider' | 'discover';
  provider: 'google' | 'github' | null;
  /** False until the server has the provider's OAuth app (see `setup`). */
  available: boolean;
  setup: string;
  /** Set when a personal access token works instead of signing in. */
  token_help: string | null;
  connection: AppConnection | null;
}

export interface AppList {
  apps: CatalogApp[];
  /** Servers added by address that sign in with OAuth. */
  custom: AppConnection[];
  is_admin: boolean;
  callback_urls: Record<string, string>;
}

export interface SignInStart {
  flow_id: string;
  authorize_url: string;
}

export async function listApps(): Promise<AppList> {
  return (await api.get<AppList>('/mcp/apps')).data;
}

export async function startAppSignIn(appId: string): Promise<SignInStart> {
  return (await api.post<SignInStart>(`/mcp/apps/${encodeURIComponent(appId)}/connect`)).data;
}

export async function startServerSignIn(serverId: string): Promise<SignInStart> {
  return (await api.post<SignInStart>(`/mcp/servers/${encodeURIComponent(serverId)}/sign-in`, {})).data;
}

export async function connectWithToken(appId: string, token: string): Promise<AppConnection> {
  return (await api.post<AppConnection>(`/mcp/apps/${encodeURIComponent(appId)}/token`, { token })).data;
}

export async function disconnectApp(appId: string): Promise<void> {
  await api.post(`/mcp/apps/${encodeURIComponent(appId)}/disconnect`);
}

export async function signOutServer(serverId: string): Promise<void> {
  await api.post(`/mcp/servers/${encodeURIComponent(serverId)}/sign-out`);
}

export async function addCustomApp(name: string, url: string): Promise<{
  connection: AppConnection;
  flow: SignInStart | null;
  error?: string;
}> {
  return (await api.post('/mcp/apps/custom', { name, url })).data;
}

export interface FlowStatus {
  status: 'pending' | 'done' | 'error';
  error: string | null;
}

export async function signInStatus(flowId: string): Promise<FlowStatus> {
  return (await api.get<FlowStatus>(`/mcp/oauth/flows/${encodeURIComponent(flowId)}`)).data;
}

/**
 * Open the app's sign-in page and wait until it's done.
 *
 * On the web the page opens in a popup. Call this straight from a click: the
 * popup is opened right away (before any request), or the browser blocks it.
 * In the desktop app it opens in the system browser. Either way the engine
 * learns the result from the callback, and we poll for it.
 */
export async function signInWithBrowser(
  start: () => Promise<SignInStart | null>,
  { pollMs = 1500, timeoutMs = 10 * 60 * 1000 }: { pollMs?: number; timeoutMs?: number } = {},
): Promise<FlowStatus> {
  const inDesktop = isDesktop();
  const popup = inDesktop ? null : window.open('', 'cognitrix-sign-in', 'popup,width=520,height=720');
  let flow: SignInStart | null;
  try {
    flow = await start();
  } catch (err) {
    popup?.close();
    throw err;
  }
  if (!flow) {
    popup?.close();
    return { status: 'done', error: null };
  }
  if (inDesktop) await desktop.openExternal(flow.authorize_url);
  else if (popup) popup.location.href = flow.authorize_url;
  else window.open(flow.authorize_url, '_blank', 'noopener');

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, pollMs));
    const status = await signInStatus(flow.flow_id);
    if (status.status !== 'pending') return status;
    if (popup && popup.closed) {
      // The person closed the window; give the callback a moment to land.
      await new Promise((r) => setTimeout(r, pollMs));
      const last = await signInStatus(flow.flow_id);
      return last.status === 'pending' ? { status: 'error', error: 'Sign-in was cancelled.' } : last;
    }
  }
  return { status: 'error', error: 'Sign-in took too long. Please try again.' };
}

/** What chat and the agent page call an app, from its server row. */
export function appLabel(catalogId: string | null | undefined, fallback: string): string {
  return CATALOG_NAMES[catalogId || ''] || fallback;
}

const CATALOG_NAMES: Record<string, string> = {
  github: 'GitHub',
  gmail: 'Gmail',
  'google-calendar': 'Google Calendar',
  'google-drive': 'Google Drive',
  'google-docs': 'Google Docs',
  'google-sheets': 'Google Sheets',
  'google-slides': 'Google Slides',
  notion: 'Notion',
  linear: 'Linear',
  sentry: 'Sentry',
};
