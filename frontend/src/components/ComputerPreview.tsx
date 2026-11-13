import { useEffect, useRef, useState } from 'react';
import { openViewer } from '@/lib/sandbox';
import { cn } from '@/lib/utils';

/** A small, view-only live picture of one agent desktop. Clicking it opens
 *  the full computer panel; nothing typed or clicked here reaches the desktop. */
export function ComputerPreview({ desktop, label, onOpen, className }: {
  /** Desktop name (see desktopName in lib/sandbox). */
  desktop: string;
  /** Whose screen it is, for screen readers. */
  label: string;
  onOpen: () => void;
  className?: string;
}) {
  const screenRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<'connecting' | 'live' | 'closed'>('connecting');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let rfb: import('@novnc/novnc/lib/rfb').default | null = null;
    let cancelled = false;
    setState('connecting');
    (async () => {
      try {
        const [{ default: RFB }, viewer] = await Promise.all([import('@novnc/novnc/lib/rfb'), openViewer(desktop)]);
        if (cancelled || !screenRef.current) return;
        rfb = new RFB(screenRef.current, viewer.url, { credentials: { password: viewer.password }, wsProtocols: ['binary'] });
        rfb.viewOnly = true;
        rfb.scaleViewport = true;
        rfb.background = 'transparent';
        rfb.addEventListener('connect', () => !cancelled && setState('live'));
        rfb.addEventListener('disconnect', () => !cancelled && setState('closed'));
      } catch {
        if (!cancelled) setState('closed');
      }
    })();
    return () => {
      cancelled = true;
      rfb?.disconnect();
    };
  }, [desktop, attempt]);

  // A dropped picture comes back by itself; the panel is still one click away.
  useEffect(() => {
    if (state !== 'closed') return;
    const timer = window.setTimeout(() => setAttempt((n) => n + 1), 5000);
    return () => window.clearTimeout(timer);
  }, [state]);

  return (
    <div className={cn('group relative aspect-[16/10] w-full overflow-hidden rounded-lg border border-line bg-[#1d2126]', className)}>
      {/* The picture is under the button, so every click opens the panel. */}
      <div ref={screenRef} aria-hidden className="pointer-events-none absolute inset-0" data-computer-preview />
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Open ${label}'s computer`}
        title="Open the computer"
        className="absolute inset-0 grid place-items-center bg-black/0 text-[12.5px] font-medium text-white transition-colors hover:bg-black/45 focus-visible:bg-black/45"
      >
        <span className="rounded-md border border-white/50 px-2.5 py-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          Open computer
        </span>
      </button>
      <span className="pointer-events-none absolute left-1.5 top-1.5 inline-flex items-center gap-1.5 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[9.5px] tracking-[0.1em] text-white">
        <span className={cn('h-1.5 w-1.5 rounded-full', state === 'live' ? 'bg-accent' : 'bg-white/50')} />
        {state === 'live' ? 'LIVE' : state === 'connecting' ? 'CONNECTING' : 'RECONNECTING'}
      </span>
    </div>
  );
}
