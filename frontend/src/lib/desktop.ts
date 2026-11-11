/**
 * Bridge to the Cognitrix desktop shell (Tauri). The same React app runs in a
 * browser, where every helper here is a harmless no-op, and inside the
 * desktop app, where the shell injects `__TAURI_INTERNALS__`.
 *
 * Commands are defined in desktop/src-tauri/src/lib.rs.
 */

export interface EngineStatus {
  status: 'starting' | 'running' | 'stopped' | 'failed';
  url: string | null;
  port: number;
  error: string | null;
  log_path: string;
  external: boolean;
}

export interface DesktopSettings {
  close_to_tray: boolean;
  notify_desktop: boolean;
  start_with_windows: boolean;
  /** Draw a pointer where agents click and type in other apps (desktop app 0.6+). */
  agent_cursor?: boolean;
  working_folder: string;
  data_folder: string;
  quick_ask_shortcut: string;
  version: string;
}

export function isDesktop(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

async function core() {
  return import('@tauri-apps/api/core');
}

export async function invokeDesktop<T>(command: string, args?: Record<string, unknown>): Promise<T | null> {
  if (!isDesktop()) return null;
  const { invoke } = await core();
  return invoke<T>(command, args);
}

/** Listen for an event from the shell. Returns an unsubscribe function. */
export async function onDesktopEvent<T>(event: string, handler: (payload: T) => void): Promise<() => void> {
  if (!isDesktop()) return () => {};
  const { listen } = await import('@tauri-apps/api/event');
  return listen<T>(event, (e) => handler(e.payload));
}

export async function windowAction(action: 'minimize' | 'toggleMaximize' | 'close' | 'hide') {
  if (!isDesktop()) return;
  const { getCurrentWindow } = await import('@tauri-apps/api/window');
  const win = getCurrentWindow();
  if (action === 'minimize') await win.minimize();
  else if (action === 'toggleMaximize') await win.toggleMaximize();
  else if (action === 'hide') await win.hide();
  else await win.close();
}

export const desktop = {
  engineStatus: () => invokeDesktop<EngineStatus>('engine_status'),
  restartEngine: () => invokeDesktop<void>('restart_engine'),
  openFolder: (kind: 'logs' | 'data' | 'working') => invokeDesktop<void>('open_folder', { kind }),
  openExternal: (url: string) => invokeDesktop<void>('open_external', { url }),
  getSettings: () => invokeDesktop<DesktopSettings>('get_desktop_settings'),
  setSettings: (patch: Partial<Pick<DesktopSettings, 'close_to_tray' | 'notify_desktop' | 'start_with_windows' | 'agent_cursor'>>) =>
    invokeDesktop<DesktopSettings>('set_desktop_settings', { patch }),
  pickWorkingFolder: () => invokeDesktop<string | null>('pick_working_folder'),
  /** Native notification; clicking it opens `path` in the main window.
   *  Resolves true when one was shown. */
  notify: (title: string, body: string, path?: string, urgent?: boolean, approval?: string) =>
    invokeDesktop<boolean>('desktop_notify', { title, body, path: path ?? null, urgent: urgent ?? false, approval: approval ?? null }),
  /** Rejects with the reason when the OS won't show it. */
  testNotification: () => invokeDesktop<void>('test_notification'),
  showMain: (path?: string) => invokeDesktop<void>('show_main_window', { path: path ?? null }),
  /** The sign-in token the shell keeps in the OS keychain, so it survives
   *  restarts and port changes (localStorage is per origin). */
  getSavedSession: () => invokeDesktop<string | null>('get_saved_session'),
  saveSession: (token: string) => invokeDesktop<void>('save_session', { token }),
  clearSession: () => invokeDesktop<void>('clear_session'),
  hideQuickAsk: () => invokeDesktop<void>('hide_quick_ask'),
  checkForUpdate: () => invokeDesktop<UpdateInfo>('check_for_update'),
  /** Downloads, installs and restarts the app. Rejects with the reason. */
  installUpdate: () => invokeDesktop<void>('install_update'),
};

export interface UpdateInfo {
  /** This build can update itself (a signed release build). */
  enabled: boolean;
  current: string;
  /** The newer version, when there is one. */
  available: string | null;
  notes: string | null;
}

export interface SystemNotice {
  title: string;
  body: string;
  /** In-app route to open when the notification is clicked. */
  href?: string;
  /** Approvals and questions: stays on screen until the person acts. */
  urgent?: boolean;
  /** Browser notifications with the same tag replace each other. */
  tag?: string;
  /** An approval's id: Windows shows Approve and Deny buttons, which the
   *  shell reports back as a `notification-action` event. */
  approval?: string;
}

/** Show a system notification: a native Windows toast in the desktop app,
 *  the browser's Notification API on the web (only if the person already
 *  allowed it). Resolves true when one was shown. */
export async function systemNotify({ title, body, href, urgent, tag, approval }: SystemNotice): Promise<boolean> {
  if (isDesktop()) {
    try {
      return Boolean(await desktop.notify(title, body, href, urgent, approval));
    } catch {
      return false;
    }
  }
  if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    const n = new Notification(title, { body, ...(tag ? { tag } : {}) });
    if (href) {
      n.onclick = () => {
        window.focus();
        window.location.assign(href);
      };
    }
    return true;
  }
  return false;
}
