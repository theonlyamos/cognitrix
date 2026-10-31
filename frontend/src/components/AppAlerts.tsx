import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useOptionalNotifications, type AppNotification } from '@/context/NotificationsContext';
import { desktop } from '@/lib/desktop';

function Toast({ n, onDismiss }: { n: AppNotification; onDismiss: () => void }) {
  const navigate = useNavigate();
  const urgent = n.event === 'approval' || n.event === 'question';

  const dismissRef = useRef(onDismiss);
  dismissRef.current = onDismiss;

  // Approvals and questions stay until answered or dismissed; the rest fade.
  useEffect(() => {
    if (urgent) return;
    const id = setTimeout(() => dismissRef.current(), 7000);
    return () => clearTimeout(id);
  }, [urgent]);

  return (
    <div role="status" className="w-[336px] max-w-[calc(100vw-24px)] rounded-xl border border-line bg-panel-2 p-3.5 shadow-panel">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-semibold">{n.title}</div>
          <div className="mt-1 text-[12.5px] leading-relaxed text-fg-dim">{n.body}</div>
        </div>
        <button type="button" aria-label="Dismiss" onClick={onDismiss} className="grid h-6 w-6 flex-none place-items-center rounded text-fg-dim hover:text-fg">
          <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M0 0l10 10M10 0 0 10" stroke="currentColor" /></svg>
        </button>
      </div>
      {n.href && (
        <button
          type="button"
          onClick={() => {
            onDismiss();
            navigate(n.href!);
          }}
          className="mt-3 h-8 w-full rounded-md bg-accent text-[12.5px] font-semibold text-accent-foreground"
        >
          {urgent ? 'Review' : 'Open'}
        </button>
      )}
    </div>
  );
}

/** In-app toasts (bottom right) and the "engine stopped" banner. */
export default function AppAlerts() {
  const ctx = useOptionalNotifications();
  if (!ctx) return null;
  const { toasts, dismissToast, engine } = ctx;
  const engineDown = engine && (engine.status === 'stopped' || engine.status === 'failed');

  return (
    <>
      {engineDown && (
        <div role="alert" className="flex flex-none items-center gap-3 border-b border-danger/40 bg-danger/10 px-4 py-2 text-[13px]">
          <span className="h-2 w-2 flex-none rounded-full bg-danger" aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate">
            <strong className="font-semibold">The local engine stopped.</strong>{' '}
            <span className="text-fg-dim">{engine.error ?? 'Chats and tasks are paused until it restarts.'}</span>
          </span>
          <button type="button" onClick={() => desktop.openFolder('logs')} className="h-7 rounded-md border border-line px-2.5 text-[12.5px] text-fg-dim hover:text-fg">
            Open log
          </button>
          <button type="button" onClick={() => desktop.restartEngine()} className="h-7 rounded-md bg-accent px-3 text-[12.5px] font-semibold text-accent-foreground">
            Restart engine
          </button>
        </div>
      )}
      <div className="pointer-events-none fixed bottom-3 right-3 z-[60] flex flex-col gap-2">
        {toasts.map((n) => (
          <div key={n.id} className="pointer-events-auto">
            <Toast n={n} onDismiss={() => dismissToast(n.id)} />
          </div>
        ))}
      </div>
    </>
  );
}
