// Apps: live web apps agents build and publish. See cognitrix/hosted_apps on the server.
// (Connected apps such as Gmail live in lib/apps.ts.)
import { api } from '@/lib/api';

export type AppStatus = 'building' | 'online' | 'asleep' | 'failed' | 'stopped';
export type AppRole = 'owner' | 'edit' | 'view';
export type AppAccess = 'private' | 'people' | 'workspace';

export interface AppSummary {
  id: string;
  slug: string;
  title: string;
  description: string;
  status: AppStatus;
  version: number;
  latest: number;
  last_error: string;
  runtime: 'sandbox' | 'local';
  branch: string;
  role: AppRole;
  owner: { id: string; name: string };
  agent: { id: string; name: string } | null;
  session_id: string;
  task_id: string;
  access: AppAccess;
  shared_with: Record<string, 'view' | 'edit'>;
  pinned: boolean;
  sleep_after: number;
  visits_7d: number[];
  uses_api: boolean;
  command: string;
  install: string;
  folder: string;
  link: string;
  thumb_url?: string;
  created: string;
  updated: string;
}

export interface AppVersion {
  number: number;
  status: 'building' | 'live' | 'ok' | 'failed';
  note: string;
  error: string;
  log: string;
  branch: string;
  agent: { id: string; name: string } | null;
  created: string;
}

export interface AppDetail extends AppSummary {
  versions?: AppVersion[];
  task?: { id: string; title: string; schedule: string } | null;
  people?: { id: string; name: string }[];
}

export interface AppList {
  apps: AppSummary[];
  runtime: 'sandbox' | 'local' | null;
}

export type AppsView = 'cards' | 'table';
const VIEW_KEY = 'cognitrix.appsView';

/** Live cards unless the person picked the table before. */
export function savedAppsView(): AppsView {
  try {
    return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'cards';
  } catch {
    return 'cards';
  }
}

export function saveAppsView(view: AppsView): void {
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    /* storage off: the choice lasts for this visit */
  }
}

export const STATUS_LABEL: Record<AppStatus, string> = {
  online: 'Online', asleep: 'Asleep', building: 'Building', failed: 'Failed', stopped: 'Stopped',
};

/** The app's stable link, to copy and share. */
export function appLink(app: Pick<AppSummary, 'slug'>): string {
  return `${window.location.origin}/apps/open/${app.slug}`;
}

/** A link to the running app for me (the server signs it; good for 12 hours). */
export async function openUrl(ref: string): Promise<string> {
  const { data } = await api.post<{ url: string }>(`/hosted-apps/${encodeURIComponent(ref)}/open`);
  return data.url;
}

/** Sandbox flags for the frame an app is shown in (never allow-same-origin). */
export const APP_FRAME_SANDBOX = 'allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads';

/** What a Publish App tool result says it published, or null. */
export function publishedAppFrom(toolName: string, result: string | undefined): { id: string; title: string; version: number } | null {
  if (toolName.toLowerCase().replace(/_/g, ' ').trim() !== 'publish app' || !result) return null;
  const match = /"(.{1,160}?)" \(app_id ([A-Za-z0-9_-]{1,64}), version (\d+)\)/.exec(result);
  return match ? { title: match[1], id: match[2], version: Number(match[3]) } : null;
}
