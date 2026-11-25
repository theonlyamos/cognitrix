// Apps installed on this computer from the desktop app (desktop/src-tauri/src/installed.rs),
// and what an app may ask the person for (cognitrix/hosted_apps/bridge.py on the server).
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { desktop, isDesktop, onDesktopEvent, type InstalledApp } from '@/lib/desktop';

export type AppPower = 'notify' | 'apps' | 'tray';

export interface AppPermissions {
  allowed: AppPower[];
  asked: AppPower[];
}

const TILE_COLORS = ['#c6f24e', '#7dd3fc', '#fca5a5', '#fcd34d', '#a7f3d0', '#c4b5fd', '#f9a8d4', '#fdba74'];

function hash(text: string): number {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) >>> 0;
  return h;
}

/** The letters on an app's icon: the first letter of its first two words. */
export function iconLetters(title: string): string {
  const words = title.trim().split(/\s+/).filter(Boolean);
  const letters = words.slice(0, 2).map((w) => Array.from(w)[0] ?? '').join('');
  return (letters || '?').toUpperCase();
}

export function iconColor(slug: string): string {
  return TILE_COLORS[hash(slug) % TILE_COLORS.length];
}

/** A 256 x 256 PNG for the app's shortcut, tray icon and window: its initials on a
 *  rounded tile, in a colour that stays the same for the app. */
export async function makeAppIcon(title: string, slug: string): Promise<Uint8Array> {
  const size = 256;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Could not draw the icon');
  const r = 56;
  ctx.beginPath();
  ctx.moveTo(r, 8);
  ctx.arcTo(size - 8, 8, size - 8, size - 8, r);
  ctx.arcTo(size - 8, size - 8, 8, size - 8, r);
  ctx.arcTo(8, size - 8, 8, 8, r);
  ctx.arcTo(8, 8, size - 8, 8, r);
  ctx.closePath();
  ctx.fillStyle = iconColor(slug);
  ctx.fill();
  const letters = iconLetters(title);
  ctx.fillStyle = '#16181a';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.font = `700 ${letters.length > 1 ? 104 : 132}px "Space Grotesk", system-ui, sans-serif`;
  ctx.fillText(letters, size / 2, size / 2 + 8);
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('Could not draw the icon');
  return new Uint8Array(await blob.arrayBuffer());
}

/** The app's own icon (``url``: an image on this server, PNG or SVG) as a 256 x 256 PNG
 *  for its shortcut, tray icon and window. Null when it can't be loaded or drawn. */
export async function drawAppIcon(url: string): Promise<Uint8Array | null> {
  try {
    const image = new Image();
    image.decoding = 'async';
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error('icon did not load'));
      image.src = url;
    });
    const size = 256;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    // Fit it inside the square (an SVG may have no size of its own: then it fills it).
    const w = image.naturalWidth || size;
    const h = image.naturalHeight || size;
    const scale = Math.min(size / w, size / h);
    const dw = w * scale;
    const dh = h * scale;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(image, (size - dw) / 2, (size - dh) / 2, dw, dh);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
    return blob ? new Uint8Array(await blob.arrayBuffer()) : null;
  } catch {
    return null;
  }
}

/** Install (or reinstall) an app on this computer, with its own icon when it has one. */
export async function installApp(app: { slug: string; title: string; icon_url?: string }, desktopShortcut: boolean): Promise<InstalledApp | null> {
  const icon = (app.icon_url ? await drawAppIcon(app.icon_url) : null) ?? await makeAppIcon(app.title, app.slug);
  const out = await desktop.installApp(app.slug, app.title, icon, desktopShortcut);
  rememberShortcutIcon(app);
  return out;
}

const ICON_SEEN_KEY = 'cognitrix.installedAppIcons';

/** Which version of each app's icon its shortcut on this computer was made with ('v' in icon_url). */
function shortcutIcons(): Record<string, string> {
  try {
    const raw = JSON.parse(localStorage.getItem(ICON_SEEN_KEY) || '{}');
    return raw && typeof raw === 'object' ? raw as Record<string, string> : {};
  } catch {
    return {};
  }
}

function iconVersion(url: string | undefined): string {
  if (!url) return '';
  try {
    return new URL(url, window.location.origin).searchParams.get('v') || '';
  } catch {
    return '';
  }
}

function rememberShortcutIcon(app: { slug: string; icon_url?: string }): void {
  try {
    localStorage.setItem(ICON_SEEN_KEY, JSON.stringify({ ...shortcutIcons(), [app.slug]: iconVersion(app.icon_url) }));
  } catch {
    /* storage off: the shortcut is refreshed again next time */
  }
}

/** Installed apps whose icon changed since their shortcut was made (or that were installed
 *  before apps had icons) get their shortcut redrawn, quietly. Returns how many changed. */
export async function refreshShortcutIcons(
  apps: { slug: string; title: string; icon_url?: string }[], installed: InstalledApp[],
): Promise<number> {
  if (!isDesktop() || installed.length === 0 || refreshing) return 0;
  refreshing = true;
  const seen = shortcutIcons();
  let changed = 0;
  try {
    for (const record of installed) {
      const app = apps.find((a) => a.slug === record.slug);
      if (!app?.icon_url || seen[app.slug] === iconVersion(app.icon_url)) continue;
      try {
        await installApp(app, record.desktop_shortcut);
        changed += 1;
      } catch {
        rememberShortcutIcon(app); // don't retry every few seconds; Install > Update the shortcut does
      }
    }
  } finally {
    refreshing = false;
  }
  if (changed) installedChanged();
  return changed;
}

let refreshing = false;

export const INSTALLED_EVENT = 'cognitrix:installed-apps';

/** Tell other parts of this window that the installed list changed. */
export function installedChanged(): void {
  window.dispatchEvent(new Event(INSTALLED_EVENT));
}

/** This computer's installed apps (always empty outside the desktop app). */
export function useInstalledApps() {
  const [apps, setApps] = useState<InstalledApp[]>([]);
  const [loaded, setLoaded] = useState(!isDesktop());
  const load = useCallback(async () => {
    if (!isDesktop()) return;
    try {
      setApps((await desktop.listInstalledApps()) ?? []);
    } catch {
      setApps([]);
    } finally {
      setLoaded(true);
    }
  }, []);
  useEffect(() => {
    void load();
    const again = () => void load();
    window.addEventListener(INSTALLED_EVENT, again);
    window.addEventListener('focus', again);
    return () => {
      window.removeEventListener(INSTALLED_EVENT, again);
      window.removeEventListener('focus', again);
    };
  }, [load]);
  return { apps, loaded, reload: load };
}

export function useInstalledApp(slug: string) {
  const { apps, loaded, reload } = useInstalledApps();
  return { installed: apps.find((a) => a.slug === slug) ?? null, loaded, reload };
}

/** What I allowed this app (notifications, connected apps). */
export async function getPermissions(appId: string): Promise<AppPermissions> {
  const { data } = await api.get<AppPermissions>(`/hosted-apps/${encodeURIComponent(appId)}/permissions`);
  return { allowed: data.allowed ?? [], asked: data.asked ?? [] };
}

export async function setPermission(appId: string, power: Exclude<AppPower, 'tray'>, allowed: boolean): Promise<AppPermissions> {
  const { data } = await api.put<AppPermissions>(`/hosted-apps/${encodeURIComponent(appId)}/permissions`, { power, allowed });
  return { allowed: data.allowed ?? [], asked: data.asked ?? [] };
}

/** A tray menu click, for the app shown in this window. */
export function onTrayClick(slug: string, handler: (id: string) => void): Promise<() => void> {
  return onDesktopEvent<{ slug: string; id: string }>('app-tray-click', (payload) => {
    if (payload?.slug === slug) handler(String(payload.id ?? ''));
  });
}
