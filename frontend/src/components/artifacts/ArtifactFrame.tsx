import { useEffect, useRef } from 'react';
import { errorMessage } from '@/lib/api';
import { useTheme } from '@/context/ThemeContext';
import { bridgeCall, frameSrc, type CommentAnchor, type PagePower } from '@/lib/artifactPages';

export interface FramePin { id: string; n: number; x: number; y: number; title: string; active: boolean }

interface ArtifactFrameProps {
  /** "/api/v1/artifact-frame/..." from the API. */
  url: string;
  title: string;
  /** Signed-in viewers: the page id its window.cognitrix calls go to. */
  pageId?: string;
  /** Powers the viewer allowed. Calls outside it are refused here and on the server. */
  allowed?: PagePower[];
  pins?: FramePin[];
  /** True while the next click on the page places a comment. */
  picking?: boolean;
  onPicked?: (anchor: CommentAnchor) => void;
  onPin?: (id: string) => void;
  className?: string;
}

interface FrameMessage {
  cognitrix?: number;
  type?: string;
  id?: string;
  kind?: string;
  payload?: Record<string, unknown>;
  anchor?: CommentAnchor;
}

function saveFile(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime || 'text/plain' });
  const href = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = filename.replace(/[\\/:*?"<>|]/g, '_').slice(0, 120) || 'download.txt';
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}

/** One version of an artifact page, in a sandboxed iframe. The page has no
 *  access to this window; what it asks for arrives as messages handled here. */
export function ArtifactFrame({ url, title, pageId, allowed = [], pins, picking = false, onPicked, onPin, className }: ArtifactFrameProps) {
  const ref = useRef<HTMLIFrameElement>(null);
  const { theme } = useTheme();
  const live = useRef({ pageId, allowed, pins, picking, onPicked, onPin });
  live.current = { pageId, allowed, pins, picking, onPicked, onPin };

  const post = (message: Record<string, unknown>) => {
    // The frame's origin is opaque (sandboxed), so there is no origin to name.
    ref.current?.contentWindow?.postMessage({ cognitrix: 1, ...message }, '*');
  };

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (!ref.current || event.source !== ref.current.contentWindow) return;
      const message = (event.data || {}) as FrameMessage;
      if (!message.cognitrix) return;
      const now = live.current;
      if (message.type === 'ready') {
        post({ type: 'pins', pins: now.pins || [] });
        post({ type: 'pick', on: now.picking });
      } else if (message.type === 'picked' && message.anchor) {
        now.onPicked?.(message.anchor);
      } else if (message.type === 'pin' && message.id) {
        now.onPin?.(message.id);
      } else if (message.type === 'call' && message.id && message.kind) {
        const { id, kind } = message;
        const payload = message.payload || {};
        const power = kind.split('.')[0] as PagePower;
        const answer = (value: unknown, error?: string) => post({ type: 'result', id, value, error });
        if (!now.allowed.includes(power)) {
          answer(null, now.pageId ? `Not allowed: the viewer has not let this page use "${power}".` : 'Sign in to Cognitrix to use this.');
        } else if (kind === 'download') {
          saveFile(String(payload.filename || ''), String(payload.content ?? ''), String(payload.mime || ''));
          answer(true);
        } else if (now.pageId) {
          bridgeCall(now.pageId, kind, payload).then((value) => answer(value), (e) => answer(null, errorMessage(e, 'That did not work.')));
        } else {
          answer(null, 'Sign in to Cognitrix to use this.');
        }
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  useEffect(() => { post({ type: 'pins', pins: pins || [] }); }, [pins]);
  useEffect(() => { post({ type: 'pick', on: picking }); }, [picking]);

  return (
    <iframe
      ref={ref}
      key={url}
      src={frameSrc(url)}
      title={title}
      // The server also sends this as a CSP sandbox, which is what counts;
      // the attribute covers a proxy that strips the header.
      sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-modals allow-forms"
      referrerPolicy="no-referrer"
      style={{ colorScheme: theme === 'dark' ? 'dark' : 'light' }}
      className={className ?? 'h-full w-full border-0 bg-panel'}
    />
  );
}
