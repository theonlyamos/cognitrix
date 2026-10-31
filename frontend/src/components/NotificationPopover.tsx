import { useEffect, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useNotifications, type AppNotification } from '@/context/NotificationsContext';
import { cn } from '@/lib/utils';

export function timeAgo(ms: number): string {
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (seconds < 60) return 'now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

const NEEDS_YOU = new Set(['approval', 'question']);

export function NotificationRow({ n, onOpen }: { n: AppNotification; onOpen: (n: AppNotification) => void }) {
  const urgent = NEEDS_YOU.has(n.event) || n.event === 'engine' || n.event === 'task_failed';
  return (
      <button
        type="button"
        onClick={() => onOpen(n)}
        className="flex w-full items-start gap-3 rounded-lg px-2.5 py-2.5 text-left transition-colors hover:bg-panel-2"
      >
        <span
          aria-hidden="true"
          className={cn(
            'mt-1.5 h-2 w-2 flex-none rounded-full',
            n.read ? 'bg-transparent' : urgent ? 'bg-[#ffb86b]' : 'bg-accent',
          )}
        />
        <span className="min-w-0 flex-1">
          <span className={cn('block text-[13.5px]', n.read ? 'text-fg-dim' : 'font-medium text-fg')}>{n.title}</span>
          <span className="block truncate text-[12.5px] text-fg-dim">{n.body}</span>
        </span>
        <span className="flex-none font-mono text-[11px] text-fg-dim">{timeAgo(n.createdAt)}</span>
      </button>
  );
}

/** The bell's dropdown: newest notifications, split into "needs you" and the rest. */
export default function NotificationPopover({ onClose }: { onClose: () => void }) {
  const { notifications, markAllRead, markRead, inbox } = useNotifications();
  const navigate = useNavigate();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [onClose]);

  const open = (n: AppNotification) => {
    markRead(n.id);
    onClose();
    if (n.href) navigate(n.href);
  };

  const recent = notifications.slice(0, 8);

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="Notifications"
      className="absolute right-0 top-[46px] w-[380px] max-w-[calc(100vw-24px)] overflow-hidden rounded-xl border border-line bg-panel shadow-panel"
    >
      <div className="flex items-center justify-between border-b border-line px-3.5 py-3">
        <span className="text-[14px] font-semibold">Notifications</span>
        <button type="button" onClick={markAllRead} className="text-[12.5px] text-fg-dim hover:text-fg">
          Mark all read
        </button>
      </div>
      {inbox.length > 0 && (
        <Link
          to="/inbox"
          onClick={onClose}
          className="mx-2 mt-2 flex items-center gap-2 rounded-lg border border-[#ffb86b]/40 bg-[#ffb86b]/10 px-3 py-2.5 text-[13px]"
        >
          <span className="font-mono text-[10px] tracking-[0.1em] text-[#ffb86b]">NEEDS YOU · {inbox.length}</span>
          <span className="flex-1 truncate text-fg">Open your inbox to answer</span>
        </Link>
      )}
      {recent.length === 0 ? (
        <p className="px-4 py-8 text-center text-[13px] text-fg-dim">Nothing yet. Task results and approvals show up here.</p>
      ) : (
        <ul className="max-h-[360px] overflow-y-auto p-1.5">
          {recent.map((n) => (
            <li key={n.id}>
              <NotificationRow n={n} onOpen={open} />
            </li>
          ))}
        </ul>
      )}
      <div className="flex justify-between border-t border-line px-3.5 py-2.5 text-[12.5px]">
        <Link to="/inbox" onClick={onClose} className="text-accent-ink hover:underline">Open inbox</Link>
        <Link to="/notifications" onClick={onClose} className="text-fg-dim hover:text-fg">All notifications and settings</Link>
      </div>
    </div>
  );
}
