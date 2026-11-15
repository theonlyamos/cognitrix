import { useCallback, useEffect, useRef, useState } from 'react';
import { AppWindow, Maximize2, Minimize2, RotateCw, X } from 'lucide-react';
import { errorMessage } from '@/lib/api';
import { useTheme } from '@/context/ThemeContext';
import { frameSrc } from '@/lib/artifactPages';
import {
  MCP_APPS_PROTOCOL, callAppTool, getAppView, onAppViewUpdate, readAppResource, safeLink, setAppContext,
  type AppViewDetail,
} from '@/lib/appViews';
import { cn } from '@/lib/utils';

const iconButton = 'grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8';
const TEARDOWN_WAIT_MS = 400;

interface RpcMessage {
  jsonrpc?: string;
  id?: string | number;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: { code: number; message: string };
}

interface Confirming {
  name: string;
  args: unknown;
  resolve: (ok: boolean) => void;
}

interface AppViewPanelProps {
  viewId: string;
  onClose: () => void;
  /** The chat's side panel is open: go back to it. */
  onBack?: () => void;
  expanded?: boolean;
  onToggleExpand?: () => void;
  /** ``ui/message``: the screen sends a message into the chat as the person. */
  onSendMessage?: (text: string) => void;
  onGone?: () => void;
}

function hostContext(theme: string, expanded: boolean, frame: HTMLIFrameElement | null) {
  const box = frame?.getBoundingClientRect();
  return {
    theme: theme === 'dark' ? 'dark' : 'light',
    displayMode: expanded ? 'fullscreen' : 'inline',
    availableDisplayModes: ['inline', 'fullscreen'],
    containerDimensions: box ? { height: Math.round(box.height), maxWidth: Math.round(box.width) } : undefined,
    locale: navigator.language,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    userAgent: 'Cognitrix',
    platform: '__TAURI_INTERNALS__' in window ? 'desktop' : 'web',
    deviceCapabilities: { touch: navigator.maxTouchPoints > 0, hover: window.matchMedia?.('(hover: hover)').matches ?? true },
  };
}

/** A connected app's own screen (MCP Apps), beside the chat.
 *
 *  The screen runs in a sandboxed frame with an opaque origin; it speaks
 *  JSON-RPC over postMessage and this panel is its host: it answers
 *  ``ui/initialize``, sends the tool's input and result, runs ``tools/call``
 *  on the same app (asking the person first when the server says so), and
 *  passes ``ui/message`` into the chat. */
export function AppViewPanel({ viewId, onClose, onBack, expanded = false, onToggleExpand, onSendMessage, onGone }: AppViewPanelProps) {
  const { theme } = useTheme();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [view, setView] = useState<AppViewDetail | null>(null);
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const [confirming, setConfirming] = useState<Confirming | null>(null);
  const ready = useRef(false);
  const sentVersion = useRef(0);
  const seq = useRef(0);
  const waiting = useRef(new Map<string | number, (message: RpcMessage) => void>());
  const frameUrlRef = useRef<string | null>(null);
  const viewIdRef = useRef(viewId);
  viewIdRef.current = viewId;
  const live = useRef({ view, theme, expanded, onSendMessage, onToggleExpand });
  live.current = { view, theme, expanded, onSendMessage, onToggleExpand };

  const post = useCallback((message: RpcMessage) => {
    // The frame's origin is opaque (sandboxed), so there is no origin to name.
    frameRef.current?.contentWindow?.postMessage({ jsonrpc: '2.0', ...message }, '*');
  }, []);

  const sendResult = useCallback((detail: AppViewDetail | null) => {
    if (!detail || !ready.current || sentVersion.current === detail.version) return;
    sentVersion.current = detail.version;
    post({ method: 'ui/notifications/tool-input', params: { arguments: detail.tool_input || {} } });
    post({ method: 'ui/notifications/tool-result', params: detail.tool_result || { content: [] } });
  }, [post]);

  const load = useCallback(async (fresh: boolean) => {
    try {
      const detail = await getAppView(viewId);
      setView(detail);
      setError(detail.error || '');
      // A new frame address only when the screen itself has to load again;
      // a new result goes into the running screen.
      if (fresh || !frameUrlRef.current) {
        frameUrlRef.current = detail.frame_url;
        setFrameUrl(detail.frame_url);
        ready.current = false;
        sentVersion.current = 0;
      } else {
        sendResult(detail);
      }
    } catch (e) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      if (status === 404) onGone?.();
      setError(errorMessage(e, 'Could not open this app.'));
    }
  }, [viewId, onGone, sendResult]);

  useEffect(() => {
    frameUrlRef.current = null;
    setConfirming((open) => { open?.resolve(false); return null; });
    setView(null);
    setFrameUrl(null);
    void load(true);
  }, [viewId, load]);

  useEffect(() => onAppViewUpdate((id) => { if (id === viewId) void load(false); }), [viewId, load]);

  const askFirst = (name: string, args: unknown) => new Promise<boolean>((resolve) => setConfirming({ name, args, resolve }));

  const answer = useCallback(async (message: RpcMessage, source: MessageEventSource) => {
    const { id, method, params = {} } = message;
    // An answer goes only to the screen that asked, and only while it is
    // still the one showing: a slow reply must never reach another app's screen.
    const askedFor = viewId;
    const send = (body: RpcMessage) => {
      if (frameRef.current?.contentWindow !== source || viewIdRef.current !== askedFor) return;
      (source as Window).postMessage({ jsonrpc: '2.0', ...body }, '*');
    };
    const reply = (result: unknown) => send({ id, result });
    const fail = (code: number, text: string) => send({ id, error: { code, message: text } });
    const now = live.current;
    try {
      switch (method) {
        case 'ui/initialize':
          reply({
            protocolVersion: MCP_APPS_PROTOCOL,
            hostInfo: { name: 'Cognitrix', version: '1' },
            hostCapabilities: { openLinks: {}, serverTools: {}, serverResources: {}, logging: {}, updateModelContext: { text: {} }, message: { text: {} } },
            hostContext: hostContext(now.theme, now.expanded, frameRef.current),
          });
          return;
        case 'ping':
          reply({});
          return;
        case 'tools/call': {
          const name = String(params.name || '');
          const args = params.arguments ?? {};
          try {
            reply(await callAppTool(viewId, name, args));
          } catch (e) {
            const status = (e as { response?: { status?: number } })?.response?.status;
            if (status !== 409) throw e;
            if (!(await askFirst(name, args))) {
              fail(-32001, 'The person said no.');
              return;
            }
            reply(await callAppTool(viewId, name, args, true));
          }
          return;
        }
        case 'resources/read':
          reply(await readAppResource(viewId, String(params.uri || '')));
          return;
        case 'ui/open-link': {
          const url = safeLink(params.url);
          if (!url) {
            fail(-32602, 'Only web links can be opened.');
            return;
          }
          window.open(url, '_blank', 'noopener,noreferrer');
          reply({});
          return;
        }
        case 'ui/message': {
          const blocks = Array.isArray(params.content) ? params.content : [params.content];
          const text = blocks
            .map((b) => (b && typeof b === 'object' && (b as { type?: string }).type === 'text' ? String((b as { text?: unknown }).text ?? '') : ''))
            .join('\n').trim();
          if (!text || !now.onSendMessage) {
            fail(-32602, 'Nothing to send.');
            return;
          }
          now.onSendMessage(text.slice(0, 4000));
          reply({});
          return;
        }
        case 'ui/update-model-context':
          await setAppContext(viewId, params.content, params.structuredContent);
          reply({});
          return;
        case 'ui/request-display-mode': {
          const wanted = params.mode === 'fullscreen';
          if (wanted !== now.expanded && now.onToggleExpand && window.matchMedia?.('(min-width: 768px)').matches) now.onToggleExpand();
          reply({ mode: (now.onToggleExpand && window.matchMedia?.('(min-width: 768px)').matches ? wanted : now.expanded) ? 'fullscreen' : 'inline' });
          return;
        }
        default:
          fail(-32601, `Not supported: ${method}`);
      }
    } catch (e) {
      fail(-32000, errorMessage(e, 'That did not work.'));
    }
  }, [viewId]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (!frameRef.current || event.source !== frameRef.current.contentWindow) return;
      const message = (event.data || {}) as RpcMessage;
      if (message.jsonrpc !== '2.0') return;
      if (message.method && message.id !== undefined) {
        void answer(message, event.source!);
      } else if (message.method === 'ui/notifications/initialized') {
        ready.current = true;
        sendResult(live.current.view);
      } else if (!message.method && message.id !== undefined) {
        const done = waiting.current.get(message.id);
        waiting.current.delete(message.id);
        done?.(message);
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [answer, sendResult]);

  // A result that arrived before the screen was ready goes in once it is.
  useEffect(() => { if (view) sendResult(view); }, [view, sendResult]);

  useEffect(() => {
    if (ready.current) post({ method: 'ui/notifications/host-context-changed', params: hostContext(theme, expanded, frameRef.current) });
  }, [theme, expanded, post]);

  const close = async () => {
    if (ready.current) {
      const id = `t${++seq.current}`;
      await new Promise<void>((resolve) => {
        const timer = window.setTimeout(resolve, TEARDOWN_WAIT_MS);
        waiting.current.set(id, () => { window.clearTimeout(timer); resolve(); });
        post({ id, method: 'ui/resource-teardown', params: { reason: 'closed' } });
      });
    }
    onClose();
  };

  const title = view?.title || 'Connected app';
  return (
    <section aria-label={`App: ${title}`} className="relative flex h-full min-h-0 flex-col bg-bg">
      <header className="flex min-h-12 flex-none items-center gap-1.5 border-b border-line py-1.5 pl-2 pr-2 md:px-3">
        {onBack && !expanded && (
          <button type="button" onClick={onBack} className="inline-flex h-9 flex-none items-center gap-1 rounded-lg px-2 font-mono text-[11.5px] text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8">
            <span aria-hidden>‹</span> This chat
          </button>
        )}
        <AppWindow size={15} className="ml-1 hidden flex-none text-fg-dim sm:block" aria-hidden />
        <h2 className="min-w-0 flex-shrink truncate text-[14px] font-semibold" title={title}>{title}</h2>
        {view && <span className="flex-none rounded-md bg-accent/15 px-1.5 py-0.5 font-mono text-[10.5px] text-accent-ink">{view.app}</span>}
        <span className="min-w-1 flex-1" />
        <button type="button" onClick={() => { setReloadKey((k) => k + 1); void load(true); }} aria-label="Reload the app" title="Reload" className={iconButton}>
          <RotateCw size={15} />
        </button>
        {onToggleExpand && (
          <button type="button" onClick={onToggleExpand} aria-label={expanded ? 'Shrink the panel' : 'Expand the panel'} title={expanded ? 'Shrink' : 'Expand'} className={cn(iconButton, 'hidden md:grid')}>
            {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          </button>
        )}
        <button type="button" onClick={() => void close()} aria-label="Close the app" className={iconButton}><X size={16} /></button>
      </header>

      <div className="relative min-h-0 flex-1">
        {frameUrl ? (
          <iframe
            ref={frameRef}
            key={`${frameUrl}:${reloadKey}`}
            src={frameSrc(frameUrl)}
            title={title}
            // The server also sends this as a CSP sandbox, which is what counts.
            sandbox="allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals"
            referrerPolicy="no-referrer"
            style={{ colorScheme: theme === 'dark' ? 'dark' : 'light' }}
            className={cn('h-full w-full border-0 bg-panel', view?.prefers_border && 'm-2 h-[calc(100%-1rem)] w-[calc(100%-1rem)] rounded-lg border border-line')}
          />
        ) : error ? (
          <div role="alert" className="p-4 text-[13px] text-fg-dim">
            <p>{error}</p>
            <button type="button" onClick={() => void load(true)} className="mt-3 rounded-md border border-line px-2.5 py-1 text-[12.5px] hover:border-fg-dim">Try again</button>
          </div>
        ) : (
          <div role="status" className="flex h-full items-center justify-center font-mono text-[12px] text-fg-dim">opening app…</div>
        )}

        {confirming && (
          <div className="absolute inset-0 z-10 flex items-end justify-center bg-black/30 p-3 sm:items-center">
            <div role="alertdialog" aria-labelledby="app-confirm-title" className="w-full max-w-sm rounded-xl border border-line bg-panel p-4 shadow-xl">
              <h3 id="app-confirm-title" className="text-[14px] font-semibold">
                Let {view?.app || 'this app'} run {confirming.name.replace(/[_-]+/g, ' ')}?
              </h3>
              <p className="mt-1 text-[12.5px] text-fg-dim">The app's screen asked for this. It can change things in {view?.app || 'the app'}.</p>
              {!!confirming.args && typeof confirming.args === 'object' && Object.keys(confirming.args as object).length > 0 && (
                <pre className="mt-2 max-h-32 overflow-auto rounded border border-line bg-panel-2 p-2 font-mono text-[11px] text-fg-dim">{JSON.stringify(confirming.args, null, 2)}</pre>
              )}
              <div className="mt-3 flex justify-end gap-2">
                <button type="button" autoFocus onClick={() => { confirming.resolve(false); setConfirming(null); }}
                  className="min-h-9 rounded-md border border-line px-3 text-[13px] hover:border-fg-dim">Don't run</button>
                <button type="button" onClick={() => { confirming.resolve(true); setConfirming(null); }}
                  className="min-h-9 rounded-md bg-fg px-3 text-[13px] font-medium text-bg">Run it</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
