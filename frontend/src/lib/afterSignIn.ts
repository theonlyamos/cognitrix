// Where to go after signing in, when a signed-out person opened a link such as
// an app's /apps/open/<slug>. Only paths inside this site are kept.
const KEY = 'cognitrix.afterSignIn';

export function rememberAfterSignIn(path: string): void {
  if (!path.startsWith('/apps/open/')) return;
  try {
    sessionStorage.setItem(KEY, path);
  } catch {
    /* storage off: they land on the dashboard */
  }
}

export function takeAfterSignIn(fallback = '/dashboard'): string {
  try {
    const path = sessionStorage.getItem(KEY) || '';
    sessionStorage.removeItem(KEY);
    return path.startsWith('/') && !path.startsWith('//') ? path : fallback;
  } catch {
    return fallback;
  }
}
