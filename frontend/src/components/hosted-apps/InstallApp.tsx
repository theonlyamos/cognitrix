import { useEffect, useRef, useState } from 'react';
import { Bell, Check, Download, PanelTop, Plug, Trash2, X } from 'lucide-react';
import { Toggle } from '@/pages/Notifications';
import { errorMessage } from '@/lib/api';
import { desktop, isDesktop, type InstalledApp } from '@/lib/desktop';
import { openAppWindow } from '@/lib/hostedApps';
import {
  getPermissions, iconColor, iconLetters, installApp, installedChanged, setPermission, type AppPermissions,
} from '@/lib/installedApps';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

const onMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform || navigator.userAgent);
const onWindows = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent);

/** The app's icon as the shortcut will show it: its own image (``src``), or its
 *  initials on a coloured tile while it has none or the image can't load. */
export function AppIcon({ title, slug, src, size = 40, className }: {
  title: string; slug: string; src?: string; size?: number; className?: string;
}) {
  const [broken, setBroken] = useState('');
  if (src && broken !== src) {
    return (
      <img src={src} alt="" aria-hidden draggable={false} loading="lazy" onError={() => setBroken(src)}
        className={cn('flex-none object-cover', className)}
        style={{ width: size, height: size, borderRadius: size * 0.22 }} />
    );
  }
  return (
    <span aria-hidden className={cn('grid flex-none place-items-center font-semibold text-[#16181a]', className)}
      style={{ width: size, height: size, borderRadius: size * 0.22, background: iconColor(slug), fontSize: size * 0.38 }}>
      {iconLetters(title)}
    </span>
  );
}

const button = 'inline-flex h-9 flex-none items-center gap-1.5 rounded-md border border-line bg-panel px-3 text-[13px] font-medium hover:bg-panel-2 disabled:opacity-50 md:h-8';

/** "Install" on an app's page (desktop app only): a shortcut that opens it in its own window. */
export function InstallAppButton({ app, installed, onChanged, className }: {
  app: { slug: string; title: string; icon_url?: string };
  installed: InstalledApp | null;
  onChanged: () => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!isDesktop()) return null;
  return (
    <>
      <button type="button" className={cn(button, className)} onClick={() => setOpen(true)}
        title={installed ? tr('ui.hostedApps.installApp.installedOnThisComputer') : tr('ui.hostedApps.installApp.installAsAnAppOn')}>
        {installed ? <Check size={14} /> : <Download size={14} />}{installed ? tr('ui.hostedApps.installApp.installed') : tr('ui.hostedApps.installApp.install')}
      </button>
      {open && <InstallDialog app={app} installed={installed} onClose={() => setOpen(false)} onChanged={onChanged} />}
    </>
  );
}

function InstallDialog({ app, installed, onClose, onChanged }: {
  app: { slug: string; title: string; icon_url?: string };
  installed: InstalledApp | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [shortcut, setShortcut] = useState(installed?.desktop_shortcut ?? false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [done, setDone] = useState<InstalledApp | null>(null);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const act = async (name: string, work: () => Promise<void>) => {
    setBusy(name);
    setError('');
    try {
      await work();
      installedChanged();
      onChanged();
    } catch (e) {
      setError(typeof e === 'string' ? e : errorMessage(e, tr('ui.hostedApps.installApp.thatDidNotWork')));
    } finally {
      setBusy('');
    }
  };
  const install = () => act('install', async () => { setDone(await installApp(app, shortcut)); });
  const uninstall = () => act('uninstall', async () => { await desktop.uninstallApp(app.slug); onClose(); });
  const place = (done ?? installed)?.place || (onMac ? tr('ui.hostedApps.installApp.placeMac') : onWindows ? tr('ui.hostedApps.installApp.placeWindows') : tr('ui.hostedApps.installApp.placeLinux'));
  const where = onMac
    ? tr('ui.hostedApps.installApp.whereMac')
    : onWindows
      ? tr('ui.hostedApps.installApp.whereWindows')
      : tr('ui.hostedApps.installApp.whereLinux');

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="install-app-title"
        className="flex w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[460px] md:rounded-2xl">
        <header className="flex items-center gap-3 border-b border-line px-4 py-3.5">
          <AppIcon title={app.title} slug={app.slug} src={app.icon_url} />
          <span className="min-w-0 flex-1">
            <span id="install-app-title" className="block truncate text-[15px] font-semibold">
              {installed && !done ? tr('ui.hostedApps.installApp.titleIsInstalled', { title: app.title }) : done ? tr('ui.hostedApps.installApp.titleIsInstalled', { title: app.title }) : tr('ui.hostedApps.installApp.installTitle', { title: app.title })}
            </span>
            <span className="block text-[12.5px] text-fg-dim">{installed || done ? tr('ui.hostedApps.installApp.findItInPlace', { place }) : tr('ui.hostedApps.installApp.opensInItsOwnWindow')}</span>
          </span>
          <button ref={close} type="button" onClick={onClose} disabled={busy !== ''} aria-label={tr('ui.hostedApps.installApp.close')}
            className="grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg"><X size={16} /></button>
        </header>
        <div className="flex flex-col gap-3 px-4 py-4 text-[13px]">
          {!installed && !done && <p className="text-fg-dim">{where}</p>}
          <p className="text-fg-dim">{tr('ui.hostedApps.installApp.itRunsOnCognitrixSo')}</p>
          {!onMac && (
            <label className="flex cursor-pointer items-center gap-2.5">
              <input type="checkbox" className="h-4 w-4 accent-accent-ink" checked={shortcut} disabled={busy !== ''}
                onChange={(e) => setShortcut(e.target.checked)} />
              {tr('ui.hostedApps.installApp.alsoPutAShortcutOn')}
            </label>
          )}
          {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
        </div>
        <footer className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
          {installed && !done && (
            <button type="button" className={cn(button, 'text-danger-ink')} disabled={busy !== ''} onClick={() => void uninstall()}>
              <Trash2 size={14} />{busy === 'uninstall' ? tr('ui.hostedApps.installApp.removing') : tr('ui.hostedApps.installApp.uninstall')}
            </button>
          )}
          <span className="flex-1" />
          {done ? (
            <>
              <button type="button" className={button} onClick={onClose}>{tr('ui.hostedApps.installApp.done')}</button>
              <button type="button" className={cn(button, 'border-transparent bg-accent text-accent-foreground hover:bg-accent/90')}
                onClick={() => { void openAppWindow(app); onClose(); }}>{tr('ui.hostedApps.installApp.openItNow')}</button>
            </>
          ) : (
            <button type="button" className={cn(button, 'border-transparent bg-accent text-accent-foreground hover:bg-accent/90')}
              disabled={busy !== ''} onClick={() => void install()}>
              <Download size={14} />{busy === 'install' ? tr('ui.hostedApps.installApp.installing') : installed ? tr('ui.hostedApps.installApp.updateTheShortcut') : tr('ui.hostedApps.installApp.install')}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

/** What I let this app do (its page, Overview): notifications, connected apps, the tray icon. */
export function AppPermissionsPanel({ app, installed, onChanged }: {
  app: { id: string; slug: string };
  installed: InstalledApp | null;
  onChanged: () => void;
}) {
  const [perms, setPerms] = useState<AppPermissions | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    getPermissions(app.id).then((p) => { if (live) setPerms(p); }).catch(() => { if (live) setPerms({ allowed: [], asked: [] }); });
    return () => { live = false; };
  }, [app.id]);

  const change = async (power: 'notify' | 'apps', allowed: boolean) => {
    setError('');
    try {
      setPerms(await setPermission(app.id, power, allowed));
      if (allowed && power === 'notify' && !isDesktop() && typeof Notification !== 'undefined' && Notification.permission === 'default') {
        await Notification.requestPermission().catch(() => undefined);
      }
    } catch (e) {
      setError(errorMessage(e, tr('ui.hostedApps.installApp.couldNotSaveThat')));
    }
  };
  const changeTray = async (allowed: boolean) => {
    setError('');
    try {
      await desktop.setAppTrayAllowed(app.slug, allowed);
      installedChanged();
      onChanged();
    } catch (e) {
      setError(typeof e === 'string' ? e : errorMessage(e, tr('ui.hostedApps.installApp.couldNotSaveThat')));
    }
  };
  if (!perms) return null;
  const row = (icon: JSX.Element, label: string, detail: string, toggle: JSX.Element) => (
    <div className="flex items-start gap-2.5 py-2">
      <span className="mt-0.5 flex-none text-fg-dim">{icon}</span>
      <span className="min-w-0 flex-1"><span className="block text-[12.5px] font-medium">{label}</span><span className="block text-[11.5px] leading-snug text-fg-dim">{detail}</span></span>
      {toggle}
    </div>
  );
  return (
    <div>
      <div className="mb-1 font-mono text-[10px] tracking-[0.16em] text-fg-dim">{tr('ui.hostedApps.installApp.whatItMayDoFor')}</div>
      <div className="divide-y divide-line">
        {row(<Bell size={14} />, tr('ui.hostedApps.installApp.notifications'), tr('ui.hostedApps.installApp.notificationsDetail'),
          <Toggle label={tr('ui.hostedApps.installApp.notifications')} checked={perms.allowed.includes('notify')} onChange={(v) => void change('notify', v)} />)}
        {row(<Plug size={14} />, tr('ui.hostedApps.installApp.connectedApps'), tr('ui.hostedApps.installApp.connectedAppsDetail'),
          <Toggle label={tr('ui.hostedApps.installApp.connectedApps')} checked={perms.allowed.includes('apps')} onChange={(v) => void change('apps', v)} />)}
        {installed && row(<PanelTop size={14} />, onMac ? tr('ui.hostedApps.installApp.menuBarIcon') : tr('ui.hostedApps.installApp.trayIcon'), tr('ui.hostedApps.installApp.trayDetail'),
          <Toggle label={onMac ? tr('ui.hostedApps.installApp.menuBarIcon') : tr('ui.hostedApps.installApp.trayIcon')} checked={installed.tray_allowed} onChange={(v) => void changeTray(v)} />)}
      </div>
      {error && <p role="alert" className="mt-1 text-[12px] text-danger-ink">{error}</p>}
    </div>
  );
}
