import { Link, useNavigate } from 'react-router-dom';
import {
  EVENT_LABELS,
  useNotifications,
  type AppNotification,
  type NotificationEvent,
} from '@/context/NotificationsContext';
import { NotificationRow } from '@/components/NotificationPopover';
import { isDesktop } from '@/lib/desktop';
import { PushCard } from '@/components/PushCard';
import { tr } from '@/lib/i18n';

const EVENTS = Object.keys(EVENT_LABELS) as NotificationEvent[];

function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className="inline-flex cursor-pointer items-center justify-center">
      <input
        type="checkbox"
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        aria-label={label}
      />
      <span className="relative h-5 w-9 rounded-full bg-panel-2 ring-1 ring-line transition-colors after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-fg-dim after:transition-transform peer-checked:bg-accent peer-checked:after:translate-x-4 peer-checked:after:bg-accent-foreground peer-focus-visible:ring-2 peer-focus-visible:ring-accent peer-disabled:opacity-50" />
    </label>
  );
}

export { Toggle };

/** Where each kind of event reaches you: Settings › Notifications. */
export function NotificationPrefsCard() {
  const { prefs, setPref } = useNotifications();
  const desktopApp = isDesktop();
  return (
    <div>
          <div className="overflow-hidden rounded-[14px] border border-line bg-panel">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-line text-[12px] text-fg-dim">
                  <th scope="col" className="px-4 py-3 text-left font-normal">{tr('ui.notifications.event')}</th>
                  <th scope="col" className="w-20 px-2 py-3 font-normal">{tr('ui.notifications.inApp')}</th>
                  <th scope="col" className="w-20 px-2 py-3 font-normal">{desktopApp ? tr('ui.notifications.desktop') : tr('ui.notifications.browser')}</th>
                </tr>
              </thead>
              <tbody>
                {EVENTS.map((event) => {
                  const meta = EVENT_LABELS[event];
                  return (
                    <tr key={event} className="border-b border-line last:border-b-0">
                      <th scope="row" className="px-4 py-3 text-left font-normal">
                        <span className="block font-medium">{meta.title}</span>
                        <span className="block text-[12px] text-fg-dim">{meta.detail}</span>
                      </th>
                      <td className="px-2 py-3 text-center">
                        {meta.alwaysInApp ? (
                          <span className="text-[12px] text-fg-dim">{tr('ui.notifications.always')}</span>
                        ) : (
                          <Toggle label={tr('ui.notifications.titleInApp', { title: meta.title })} checked={prefs[event].inApp} onChange={(v) => setPref(event, 'inApp', v)} />
                        )}
                      </td>
                      <td className="px-2 py-3 text-center">
                        <Toggle label={tr('ui.notifications.titleOnValue', { title: meta.title, value: desktopApp ? 'desktop' : 'browser' })} checked={prefs[event].desktop} onChange={(v) => setPref(event, 'desktop', v)} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {!desktopApp && typeof Notification !== 'undefined' && Notification.permission === 'default' && (
            <button
              type="button"
              onClick={() => void Notification.requestPermission()}
              className="mt-3 h-9 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2"
            >
              {tr('ui.notifications.allowBrowserNotifications')}
            </button>
          )}
          {!desktopApp && <PushCard />}
    </div>
  );
}

/** Everything that happened lately, newest first: the Inbox's "Recent" tab. */
export function RecentNotifications() {
  const { notifications, markAllRead, markRead, dismiss } = useNotifications();
  const navigate = useNavigate();
  const open = (n: AppNotification) => {
    markRead(n.id);
    if (n.href) navigate(n.href);
  };
  if (notifications.length === 0) {
    return (
      <div className="grid h-full place-items-center px-6 text-center">
        <div>
          <div className="text-[15px] font-medium">{tr('ui.notifications.noNotificationsYet')}</div>
          <p className="mt-1 text-[13px] text-fg-dim">{tr('ui.notifications.finishedTasksRepliesAndAlerts')}</p>
        </div>
      </div>
    );
  }
  return (
    <div className="mx-auto w-full max-w-[760px] px-4 py-5 md:px-7">
      <div className="mb-2 flex items-center justify-end gap-4 text-[12.5px]">
        <button type="button" onClick={markAllRead} className="text-fg-dim hover:text-fg">{tr('ui.notifications.markAllRead')}</button>
        <Link to="/settings/notifications" className="text-fg-dim hover:text-fg">{tr('ui.notifications.notificationSettings')}</Link>
      </div>
      <ul className="rounded-[14px] border border-line bg-panel p-1.5">
        {notifications.map((n) => (
          <li key={n.id} className="group relative">
            <NotificationRow n={n} onOpen={open} />
            <button
              type="button"
              aria-label={tr('ui.notifications.dismissTitle', { title: n.title })}
              onClick={() => dismiss(n.id)}
              className="absolute right-2 top-2 hidden h-6 w-6 place-items-center rounded bg-panel text-fg-dim hover:text-fg group-hover:grid"
            >
              <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden="true"><path d="M0 0l10 10M10 0 0 10" stroke="currentColor" /></svg>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
