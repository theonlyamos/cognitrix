import { useNavigate } from 'react-router-dom';
import {
  EVENT_LABELS,
  useNotifications,
  type AppNotification,
  type NotificationEvent,
} from '@/context/NotificationsContext';
import { NotificationRow } from '@/components/NotificationPopover';
import { isDesktop } from '@/lib/desktop';

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
      <span className="relative h-5 w-9 rounded-full bg-panel-2 ring-1 ring-line transition-colors after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-fg-dim after:transition-transform peer-checked:bg-accent peer-checked:after:translate-x-4 peer-checked:after:bg-[#0b0c0e] peer-focus-visible:ring-2 peer-focus-visible:ring-accent peer-disabled:opacity-50" />
    </label>
  );
}

export { Toggle };

/** Notification center plus where each kind of event reaches you. */
export default function Notifications() {
  const { notifications, prefs, setPref, markAllRead, markRead, dismiss } = useNotifications();
  const navigate = useNavigate();
  const desktopApp = isDesktop();

  const open = (n: AppNotification) => {
    markRead(n.id);
    if (n.href) navigate(n.href);
  };

  return (
    <div className="flex h-full min-w-0 flex-1 overflow-y-auto bg-bg text-fg">
      <div className="mx-auto grid w-full max-w-[1180px] gap-6 px-7 py-6 max-md:px-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <section aria-labelledby="prefs-title">
          <h1 id="prefs-title" className="text-[22px] font-semibold">Notifications</h1>
          <p className="mt-1 text-[13.5px] text-fg-dim">Choose where each event reaches you. Anything that pauses work always shows in the app.</p>
          <div className="mt-5 overflow-hidden rounded-[14px] border border-line bg-panel">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-line font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">
                  <th scope="col" className="px-4 py-3 text-left font-normal">EVENT</th>
                  <th scope="col" className="w-20 px-2 py-3 font-normal">IN APP</th>
                  <th scope="col" className="w-20 px-2 py-3 font-normal">{desktopApp ? 'DESKTOP' : 'BROWSER'}</th>
                  <th scope="col" className="w-20 px-2 py-3 font-normal">PHONE</th>
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
                          <span className="font-mono text-[11px] text-fg-dim">always</span>
                        ) : (
                          <Toggle label={`${meta.title} in app`} checked={prefs[event].inApp} onChange={(v) => setPref(event, 'inApp', v)} />
                        )}
                      </td>
                      <td className="px-2 py-3 text-center">
                        <Toggle label={`${meta.title} on ${desktopApp ? 'desktop' : 'browser'}`} checked={prefs[event].desktop} onChange={(v) => setPref(event, 'desktop', v)} />
                      </td>
                      <td className="px-2 py-3 text-center">
                        <span className="font-mono text-[11px] text-fg-dim" title="Phone pairing is coming in a later update">soon</span>
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
              Allow browser notifications
            </button>
          )}
        </section>

        <section aria-labelledby="center-title" className="flex min-h-0 flex-col rounded-[14px] border border-line bg-panel">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 id="center-title" className="text-[15px] font-semibold">Recent</h2>
            <button type="button" onClick={markAllRead} className="text-[12.5px] text-fg-dim hover:text-fg">Mark all read</button>
          </div>
          {notifications.length === 0 ? (
            <p className="px-4 py-10 text-center text-[13px] text-fg-dim">No notifications yet.</p>
          ) : (
            <ul className="p-1.5">
              {notifications.map((n) => (
                <li key={n.id} className="group relative">
                  <NotificationRow n={n} onOpen={open} />
                  <button
                    type="button"
                    aria-label={`Dismiss ${n.title}`}
                    onClick={() => dismiss(n.id)}
                    className="absolute right-2 top-2 hidden h-6 w-6 place-items-center rounded bg-panel text-fg-dim hover:text-fg group-hover:grid"
                  >
                    <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden="true"><path d="M0 0l10 10M10 0 0 10" stroke="currentColor" /></svg>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
