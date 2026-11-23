// Chill mode or the full app: which one the person used last, so the app
// opens in it again. Kept per device.

export type UiMode = 'chill' | 'full';

const KEY = 'uiMode';

export const CHILL_PATH = '/chill';

export function getUiMode(): UiMode {
  try {
    return localStorage.getItem(KEY) === 'chill' ? 'chill' : 'full';
  } catch {
    return 'full';
  }
}

export function setUiMode(mode: UiMode): void {
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* not remembered */
  }
}

/** Ctrl+Shift+. (Cmd+Shift+. on a Mac) switches between the two. */
export function isModeSwitchKey(event: KeyboardEvent): boolean {
  return (event.ctrlKey || event.metaKey) && event.shiftKey && !event.altKey
    && (event.code === 'Period' || event.key === '>' || event.key === '.');
}

/** Pages the app opens on by itself; the remembered mode may replace them. */
export function isLandingPath(pathname: string, search: string): boolean {
  return (pathname === '/home' || pathname === '/dashboard') && !search;
}
