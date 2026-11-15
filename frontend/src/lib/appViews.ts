import { api } from '@/lib/api';

// Connected-app screens shown beside the chat (MCP Apps).
// See cognitrix/mcp/app_views.py on the server.

export interface AppViewSummary {
  id: string;
  app: string;
  tool: string;
  title: string;
  session_id: string;
  agent_id: string;
  version: number;
  created: string;
  updated: string;
}

export interface AppViewDetail extends AppViewSummary {
  tool_input: Record<string, unknown>;
  tool_result: Record<string, unknown>;
  frame_url: string | null;
  prefers_border?: boolean;
  error: string | null;
}

export const listAppViews = async (sessionId: string): Promise<AppViewSummary[]> =>
  (await api.get<AppViewSummary[]>('/app-views', { params: { session_id: sessionId } })).data;

export const getAppView = async (id: string): Promise<AppViewDetail> =>
  (await api.get<AppViewDetail>(`/app-views/${encodeURIComponent(id)}`)).data;

export const callAppTool = async (id: string, name: string, args: unknown, confirmed = false): Promise<Record<string, unknown>> =>
  (await api.post(`/app-views/${encodeURIComponent(id)}/call`, { name, arguments: args ?? {}, confirmed })).data;

export const readAppResource = async (id: string, uri: string): Promise<Record<string, unknown>> =>
  (await api.post(`/app-views/${encodeURIComponent(id)}/read`, { uri })).data;

export const setAppContext = async (id: string, content: unknown, structuredContent: unknown): Promise<void> => {
  await api.post(`/app-views/${encodeURIComponent(id)}/context`, { content: Array.isArray(content) ? content : null, structuredContent: structuredContent ?? null });
};

/** The screen a tool result says it opened ("... (app_view <id>, version <n>)"), or null. */
export function appViewFrom(result: string | null | undefined): { id: string; version: number; app: string } | null {
  if (!result) return null;
  const match = /(?:^|\n)(.{1,120}?) is showing this beside the chat \(app_view ([A-Za-z0-9_-]{1,64}), version (\d+)\)/.exec(result);
  return match ? { app: match[1].trim(), id: match[2], version: Number(match[3]) } : null;
}

const OPEN_EVENT = 'cognitrix:open-app-view';
const UPDATED_EVENT = 'cognitrix:app-view-updated';

/** Ask the chat on screen to open an app screen in its side panel. False when nothing took it. */
export function requestOpenAppView(id: string): boolean {
  const event = new CustomEvent(OPEN_EVENT, { detail: { id }, cancelable: true });
  return !window.dispatchEvent(event);
}

export function onOpenAppView(handler: (id: string) => void): () => void {
  const listener = (event: Event) => {
    const id = (event as CustomEvent<{ id?: string }>).detail?.id;
    if (!id) return;
    event.preventDefault();
    handler(id);
  };
  window.addEventListener(OPEN_EVENT, listener);
  return () => window.removeEventListener(OPEN_EVENT, listener);
}

/** The agent ran the screen's tool again: an open panel shows the new result in place. */
export function announceAppViewUpdate(id: string): void {
  window.dispatchEvent(new CustomEvent(UPDATED_EVENT, { detail: { id } }));
}

export function onAppViewUpdate(handler: (id: string) => void): () => void {
  const listener = (event: Event) => {
    const id = (event as CustomEvent<{ id?: string }>).detail?.id;
    if (id) handler(id);
  };
  window.addEventListener(UPDATED_EVENT, listener);
  return () => window.removeEventListener(UPDATED_EVENT, listener);
}

/** Only web links leave the app (``ui/open-link``). */
export function safeLink(url: unknown): string | null {
  if (typeof url !== 'string') return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' || parsed.protocol === 'mailto:' ? parsed.toString() : null;
  } catch {
    return null;
  }
}

export const MCP_APPS_PROTOCOL = '2026-01-26';
