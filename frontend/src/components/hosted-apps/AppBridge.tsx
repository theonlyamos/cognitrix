import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { Bell, PanelTop, Plug, ShieldAlert } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { desktop, isDesktop, systemNotify, type AppTray, type InstalledApp } from '@/lib/desktop';
import { getPermissions, installedChanged, onTrayClick, setPermission, type AppPermissions, type AppPower } from '@/lib/installedApps';
import { tr } from '@/lib/i18n';

/** What the bridge needs to know about the app it serves. */
export interface BridgeApp {
  id: string;
  slug: string;
  title: string;
}

interface Ask {
  power: AppPower;
  resolve: (allowed: boolean) => void;
}

interface Confirm {
  app: string;
  tool: string;
  args: string;
  resolve: (allowed: boolean) => void;
}

type Call = { id: string; kind: string; payload: Record<string, unknown> };

const onMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform || navigator.userAgent);
const NOTIFY_PER_MINUTE = 6;

/** What went wrong, for the app: the server's own words, or ours. */
function reasonOf(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message);
  if (typeof e === 'string') return e;
  if (e instanceof Error && !(e as { isAxiosError?: boolean }).isAxiosError) return e.message;
  return errorMessage(e, tr('ui.hostedApps.appBridge.thatDidNotWork'));
}

function text(value: unknown, max: number): string {
  return String(value ?? '').slice(0, max);
}

/**
 * The Cognitrix side of `window.cognitrix` in an app (cognitrix/hosted_apps/bridge.py).
 * The app runs in a sandboxed frame; it posts calls here, this asks the person the
 * first time (and before every change to a connected app), then does the work as them.
 */
export function useAppBridge({ frame, app, ownWindow, installed, trayItem }: {
  frame: RefObject<HTMLIFrameElement>;
  app: BridgeApp | null;
  ownWindow: boolean;
  installed: InstalledApp | null;
  /** A tray menu item that opened this window, for the app to hear once it's up. */
  trayItem?: string;
}) {
  const [ask, setAsk] = useState<Ask | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const perms = useRef<AppPermissions | null>(null);
  const tray = useRef<InstalledApp | null>(installed);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const sent = useRef<number[]>([]);
  const trayDelivered = useRef(false);
  tray.current = installed;

  useEffect(() => {
    perms.current = null;
    if (!app) return;
    getPermissions(app.id).then((p) => { perms.current = p; }).catch(() => undefined);
  }, [app?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const post = useCallback((message: Record<string, unknown>) => {
    frame.current?.contentWindow?.postMessage({ cognitrix: 1, ...message }, '*');
  }, [frame]);

  /** One question at a time, in the order the app asked. */
  const serial = useCallback(<T,>(work: () => Promise<T>): Promise<T> => {
    const next = queue.current.then(work, work);
    queue.current = next.catch(() => undefined);
    return next;
  }, []);

  const state = useCallback(async (power: AppPower): Promise<'granted' | 'denied' | 'prompt' | 'unavailable'> => {
    if (!app) return 'unavailable';
    if (power === 'tray') {
      const row = tray.current;
      if (!isDesktop() || !row) return 'unavailable';
      return row.tray_allowed ? 'granted' : row.tray_asked ? 'denied' : 'prompt';
    }
    if (power !== 'notify' && power !== 'apps') return 'unavailable';
    if (!perms.current) perms.current = await getPermissions(app.id);
    if (perms.current.allowed.includes(power)) return 'granted';
    return perms.current.asked.includes(power) ? 'denied' : 'prompt';
  }, [app]);

  /** Allowed already, or ask now. A "no" sticks until changed on the app's page. */
  const ensure = useCallback((power: AppPower): Promise<boolean> => serial(async () => {
    const now = await state(power);
    if (now === 'granted') return true;
    if (now !== 'prompt' || !app) return false;
    const yes = await new Promise<boolean>((resolve) => setAsk({ power, resolve }));
    setAsk(null);
    if (power === 'tray') {
      const row = await desktop.setAppTrayAllowed(app.slug, yes);
      if (row) tray.current = row;
      installedChanged();
    } else {
      perms.current = await setPermission(app.id, power, yes);
      if (yes && power === 'notify' && !isDesktop() && typeof Notification !== 'undefined' && Notification.permission === 'default') {
        await Notification.requestPermission().catch(() => undefined);
      }
    }
    return yes;
  }), [app, serial, state]);

  const confirmChange = useCallback((connected: string, tool: string, args: unknown) => serial(async () => {
    let shown = '';
    try { shown = JSON.stringify(args ?? {}, null, 2); } catch { shown = String(args); }
    const yes = await new Promise<boolean>((resolve) => setConfirm({ app: connected, tool, args: shown.slice(0, 1200), resolve }));
    setConfirm(null);
    return yes;
  }), [serial]);

  const run = useCallback(async ({ kind, payload }: Call): Promise<unknown> => {
    if (!app) throw new Error(tr('ui.hostedApps.appBridge.theAppIsStillOpening'));
    const power = text(payload.power, 20) as AppPower;
    switch (kind) {
      case 'permission.query':
        return state(power);
      case 'permission.request':
        return ensure(power);
      case 'notify': {
        if (!(await ensure('notify'))) throw new Error(tr('ui.hostedApps.appBridge.notificationsAreNotAllowedFor'));
        const minute = Date.now() - 60_000;
        sent.current = sent.current.filter((t) => t > minute);
        if (sent.current.length >= NOTIFY_PER_MINUTE) throw new Error(tr('ui.hostedApps.appBridge.tooManyNotificationsTryAgain'));
        sent.current.push(Date.now());
        const title = text(payload.title, 120);
        const body = text(payload.body, 400);
        if (isDesktop()) return Boolean(await desktop.appNotify(app.slug, app.title, title, body));
        return systemNotify({ title: title ? `${app.title} · ${title}` : app.title, body, tag: `app-${app.slug}` });
      }
      case 'tray.set':
      case 'tray.clear': {
        if (!isDesktop() || !tray.current) throw new Error(tr('ui.hostedApps.appBridge.installThisAppFromThe'));
        if (kind === 'tray.clear') return Boolean(await desktop.setAppTray(app.slug, null));
        if (!(await ensure('tray'))) throw new Error(tr('ui.hostedApps.appBridge.aTrayIconIsNot'));
        const items = Array.isArray(payload.items) ? payload.items : [];
        const config: AppTray = {
          tooltip: text(payload.tooltip, 120),
          title: text(payload.title, 24),
          items: items.slice(0, 12).map((i) => ({ id: text((i as { id?: unknown })?.id, 64), label: text((i as { label?: unknown })?.label, 60) })),
        };
        const row = await desktop.setAppTray(app.slug, config);
        if (row) tray.current = row;
        return true;
      }
      case 'apps.list':
      case 'apps.call': {
        if (!(await ensure('apps'))) throw new Error(tr('ui.hostedApps.appBridge.connectedAppsAreNotAllowed'));
        // Only what the app may set: `confirmed` comes from the person, below.
        const body = kind === 'apps.list' ? {} : {
          app: text(payload.app, 120), tool: text(payload.tool, 120),
          arguments: payload.arguments && typeof payload.arguments === 'object' ? payload.arguments : {},
        };
        const send = (confirmed: boolean) => api.post<{ value: unknown }>(`/hosted-apps/${encodeURIComponent(app.id)}/bridge`, { kind, payload: body, confirmed });
        try {
          return (await send(false)).data.value;
        } catch (e) {
          const detail = (e as { response?: { status?: number; data?: { detail?: { code?: string } } } })?.response;
          if (kind !== 'apps.call' || detail?.status !== 409 || detail?.data?.detail?.code !== 'confirm') throw e;
          if (!(await confirmChange(String(body.app), String(body.tool), body.arguments))) throw new Error(tr('ui.hostedApps.appBridge.youSaidNoToThis'));
          return (await send(true)).data.value;
        }
      }
      default:
        throw new Error(tr('ui.hostedApps.appBridge.unknownCallKind', { kind }));
    }
  }, [app, confirmChange, ensure, state]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const win = frame.current?.contentWindow;
      if (!win || event.source !== win) return;
      const m = event.data as { cognitrix?: number; type?: string; id?: string; kind?: string; payload?: unknown } | null;
      if (!m || m.cognitrix !== 1) return;
      if (m.type === 'hello') {
        post({ type: 'host', platform: isDesktop() ? 'desktop' : 'web', installed: Boolean(tray.current), window: ownWindow });
        if (trayItem && !trayDelivered.current) {
          trayDelivered.current = true;
          window.setTimeout(() => post({ type: 'tray-click', id: trayItem }), 300);
        }
        return;
      }
      if (m.type !== 'call' || typeof m.id !== 'string' || typeof m.kind !== 'string') return;
      const call: Call = { id: m.id.slice(0, 40), kind: m.kind, payload: (m.payload && typeof m.payload === 'object' ? m.payload : {}) as Record<string, unknown> };
      run(call)
        .then((value) => post({ type: 'result', id: call.id, value }))
        .catch((e) => post({ type: 'result', id: call.id, error: reasonOf(e) }));
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [frame, ownWindow, post, run, trayItem]);

  // Tray menu clicks while this window is open.
  useEffect(() => {
    if (!app || !isDesktop()) return;
    let stop: (() => void) | null = null;
    let live = true;
    void onTrayClick(app.slug, (id) => post({ type: 'tray-click', id })).then((fn) => { if (live) stop = fn; else fn(); });
    return () => { live = false; stop?.(); };
  }, [app?.slug, post]); // eslint-disable-line react-hooks/exhaustive-deps

  return { ask, confirm };
}

const POWER_ICON: Record<AppPower, JSX.Element> = {
  notify: <Bell size={17} />, apps: <Plug size={17} />, tray: <PanelTop size={17} />,
};

function askCopy(power: AppPower, title: string): { heading: string; detail: string } {
  if (power === 'notify') {
    return {
      heading: tr('ui.hostedApps.appBridge.letTitleSendYouNotifications', { title }),
      detail: tr('ui.hostedApps.appBridge.itCanShowSystemNotifications'),
    };
  }
  if (power === 'tray') {
    return onMac ? {
      heading: tr('ui.hostedApps.appBridge.letTitleAddAnIcon', { title }),
      detail: tr('ui.hostedApps.appBridge.itStaysThereWhileCognitrix'),
    } : {
      heading: tr('ui.hostedApps.appBridge.letTitleAddAnIcon_91fa', { title }),
      detail: tr('ui.hostedApps.appBridge.itStaysThereWhileCognitrix'),
    };
  }
  return {
    heading: tr('ui.hostedApps.appBridge.letTitleUseYourConnected', { title }),
    detail: tr('ui.hostedApps.appBridge.itCanReadFromThe'),
  };
}

const dialogBox = 'flex w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[460px] md:rounded-2xl';
const quiet = 'inline-flex h-9 items-center rounded-md border border-line px-3 text-[13px] hover:bg-panel-2 md:h-8';
const primary = 'inline-flex h-9 items-center rounded-md bg-accent px-3.5 text-[13px] font-semibold text-accent-foreground md:h-8';

/** The questions an app's calls raise: allow a power, or one change to a connected app. */
export function AppBridgeDialogs({ title, ask, confirm }: { title: string; ask: Ask | null; confirm: Confirm | null }) {
  if (!ask && !confirm) return null;
  if (confirm) {
    return (
      <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center">
        <div role="dialog" aria-modal="true" aria-label={tr('ui.hostedApps.appBridge.titleWantsToMakeA', { title })} className={dialogBox}>
          <header className="flex items-start gap-3 border-b border-line px-4 py-3.5">
            <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-warn/15 text-warn"><ShieldAlert size={17} /></span>
            <span className="min-w-0">
              <span className="block text-[15px] font-semibold">{tr('ui.hostedApps.appBridge.titleWantsToUseTool', { title, tool: confirm.tool, app: confirm.app })}</span>
              <span className="mt-0.5 block text-[12.5px] text-fg-dim">{tr('ui.hostedApps.appBridge.thisCanChangeThingsIn')}</span>
            </span>
          </header>
          <pre className="max-h-56 overflow-auto bg-bg px-4 py-3 font-mono text-[11.5px] leading-relaxed text-fg-dim">{confirm.args}</pre>
          <footer className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
            <button type="button" className={quiet} onClick={() => confirm.resolve(false)}>{tr('ui.hostedApps.appBridge.donTAllow')}</button>
            <button type="button" className={primary} onClick={() => confirm.resolve(true)} autoFocus>{tr('ui.hostedApps.appBridge.allowOnce')}</button>
          </footer>
        </div>
      </div>
    );
  }
  const power = ask!.power;
  const copy = askCopy(power, title);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center">
      <div role="dialog" aria-modal="true" aria-label={copy.heading} className={dialogBox}>
        <div className="flex items-start gap-3 px-4 py-4">
          <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-accent/15 text-accent-ink">{POWER_ICON[power]}</span>
          <span className="min-w-0">
            <span className="block text-[15px] font-semibold">{copy.heading}</span>
            <span className="mt-1 block text-[12.5px] leading-snug text-fg-dim">{copy.detail}</span>
          </span>
        </div>
        <footer className="flex items-center gap-2 border-t border-line px-4 py-3">
          <span className="min-w-0 flex-1 text-[12px] text-fg-dim">{tr('ui.hostedApps.appBridge.youCanChangeThisOn')}</span>
          <button type="button" className={quiet} onClick={() => ask!.resolve(false)}>{tr('ui.hostedApps.appBridge.donTAllow')}</button>
          <button type="button" className={primary} onClick={() => ask!.resolve(true)} autoFocus>{tr('ui.hostedApps.appBridge.allow')}</button>
        </footer>
      </div>
    </div>
  );
}
