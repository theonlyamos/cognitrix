/**
 * Links in agent replies, task results and team chat point at other sites.
 * On the web they open in a new tab (`target="_blank"`). Inside the desktop
 * app a webview can't open tabs, so a click on such a link is handed to the
 * shell, which opens it in the default browser (or mail app for mailto).
 *
 * Only http, https and mailto links leave the app; the app's own pages
 * (same origin, relative links) keep routing inside it.
 */
import { desktop, isDesktop } from '@/lib/desktop';

const BROWSER_SCHEMES = new Set(['http:', 'https:', 'mailto:']);

/** The absolute URL to open outside the app, or null to leave the click alone. */
export function externalHref(href: string | null | undefined, origin: string): string | null {
  if (!href) return null;
  let url: URL;
  try {
    url = new URL(href, origin);
  } catch {
    return null;
  }
  if (!BROWSER_SCHEMES.has(url.protocol)) return null;
  if (url.protocol !== 'mailto:' && url.origin === origin) return null;
  return url.href;
}

/** Desktop app only: send clicks on outside links to the default browser. */
export function installExternalLinkHandler(doc: Document = document): () => void {
  if (!isDesktop()) return () => {};
  const onClick = (e: MouseEvent) => {
    if (e.defaultPrevented || e.button > 1) return;
    const target = e.target as Element | null;
    const anchor = target?.closest?.('a[href]') as HTMLAnchorElement | null;
    if (!anchor || anchor.hasAttribute('download')) return;
    const url = externalHref(anchor.getAttribute('href'), doc.defaultView?.location.origin ?? '');
    if (!url) return;
    e.preventDefault();
    void desktop.openExternal(url).catch(() => {});
  };
  doc.addEventListener('click', onClick);
  doc.addEventListener('auxclick', onClick);
  return () => {
    doc.removeEventListener('click', onClick);
    doc.removeEventListener('auxclick', onClick);
  };
}
