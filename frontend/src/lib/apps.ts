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
  /** Extra permissions turned on for this app (see `CatalogApp.options`). */
  options?: string[];
  /** Turned on, but the sign-in doesn't cover them yet: reconnect to grant. */
  pending_options?: string[];
}

/** An extra permission a person can turn on for an app, e.g. sending email. */
export interface AppOption {
  id: string;
  label: string;
  description: string;
  /** True when turning it on needs a new sign-in to grant it. */
  needs_sign_in: boolean;
}

export type Provider = 'google' | 'github' | 'slack';

export interface CatalogApp {
  id: string;
  name: string;
  description: string;
  category: string;
  auth: 'provider' | 'discover';
  provider: Provider | null;
  /** False until the server has the provider's OAuth app (see `setup`). */
  available: boolean;
  setup: string;
  /** Set when a personal access token works instead of signing in. */
  token_help: string | null;
  options?: AppOption[];
  connection: AppConnection | null;
}

/** An app connected through the person's own Composio account. */
export interface ComposioConnection {
  server_id: string;
  slug: string;
  name: string;
  status: AppStatus;
  account: string;
  enabled: boolean;
}

export interface ComposioState {
  has_key: boolean;
  /** The end of the saved key, e.g. "…abcd". */
  hint: string | null;
  connections: ComposioConnection[];
}

export interface ComposioToolkit {
  slug: string;
  name: string;
  description: string;
  logo: string;
  categories: string[];
  tools_count: number;
  /** False when Composio can't sign in to it without your own OAuth app. */
  connectable: boolean;
  connection: ComposioConnection | null;
}

/** One line of a "test connection" result. `ok` null means it couldn't tell. */
export interface Check {
  label: string;
  ok: boolean | null;
  detail: string;
}

export interface CheckResult {
  ok: boolean;
  checks: Check[];
  tools?: number;
}

export interface SetupStep {
  title: string;
  body: string;
  link?: { label: string; url: string };
  copy?: { label: string; text: string };
}

/** What an admin needs to register this server with Google, GitHub or Slack. */
export interface ProviderSetup {
  id: Provider;
  label: string;
  source: 'env' | 'saved' | 'none';
  can_edit: boolean;
  client_id: string;
  env_vars: string[];
  redirect_uri: string;
  redirect_problem: string | null;
  apps: string[];
  steps: SetupStep[];
}

export interface AppList {
  apps: CatalogApp[];
  /** Servers added by address that sign in with OAuth. */
  custom: AppConnection[];
  is_admin: boolean;
  callback_urls: Record<string, string>;
  composio?: ComposioState;
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

export async function setAppOptions(appId: string, options: string[]): Promise<AppConnection> {
  return (await api.put<AppConnection>(`/mcp/apps/${encodeURIComponent(appId)}/options`, { options })).data;
}

export async function testApp(appId: string): Promise<CheckResult> {
  return (await api.post<CheckResult>(`/mcp/apps/${encodeURIComponent(appId)}/test`)).data;
}

export async function getSetupGuide(): Promise<ProviderSetup[]> {
  return (await api.get<{ providers: ProviderSetup[] }>('/mcp/apps/setup')).data.providers;
}

export async function saveProviderClient(provider: Provider, clientId: string, clientSecret: string): Promise<ProviderSetup> {
  return (await api.put<ProviderSetup>(`/mcp/apps/setup/${provider}`, { client_id: clientId, client_secret: clientSecret })).data;
}

export async function removeProviderClient(provider: Provider): Promise<ProviderSetup> {
  return (await api.delete<ProviderSetup>(`/mcp/apps/setup/${provider}`)).data;
}

export async function testProviderClient(provider: Provider): Promise<CheckResult> {
  return (await api.post<CheckResult>(`/mcp/apps/setup/${provider}/test`)).data;
}

export async function saveComposioKey(apiKey: string): Promise<{ has_key: boolean; hint: string | null }> {
  return (await api.put('/mcp/composio/key', { api_key: apiKey })).data;
}

export async function removeComposioKey(): Promise<void> {
  await api.delete('/mcp/composio/key');
}

export async function searchComposioApps(query: string, cursor?: string | null): Promise<{
  toolkits: ComposioToolkit[];
  next_cursor: string | null;
}> {
  return (await api.get('/mcp/composio/apps', { params: { query, ...(cursor ? { cursor } : {}) } })).data;
}

export async function startComposioSignIn(slug: string, name: string): Promise<SignInStart> {
  return (await api.post<SignInStart>(`/mcp/composio/apps/${encodeURIComponent(slug)}/connect`, { name })).data;
}

export async function disconnectComposioApp(serverId: string): Promise<void> {
  await api.post(`/mcp/composio/connections/${encodeURIComponent(serverId)}/disconnect`);
}

export async function testComposioApp(serverId: string): Promise<CheckResult> {
  return (await api.post<CheckResult>(`/mcp/composio/connections/${encodeURIComponent(serverId)}/test`)).data;
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
  slack: 'Slack',
};
