import { afterEach, describe, expect, it, vi } from 'vitest';

const openExternal = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
const desktopMode = vi.hoisted(() => ({ on: true }));
vi.mock('@/lib/desktop', () => ({ isDesktop: () => desktopMode.on, desktop: { openExternal } }));

import { externalHref, installExternalLinkHandler } from './externalLinks';

const ORIGIN = 'http://127.0.0.1:48210';

describe('externalHref', () => {
  it('keeps web and mail links to other places', () => {
    expect(externalHref('https://example.com/a?b=1', ORIGIN)).toBe('https://example.com/a?b=1');
    expect(externalHref('http://example.com', ORIGIN)).toBe('http://example.com/');
    expect(externalHref('mailto:someone@example.com', ORIGIN)).toBe('mailto:someone@example.com');
  });

  it('leaves the app\'s own pages and other schemes alone', () => {
    expect(externalHref('/tasks/1', ORIGIN)).toBeNull();
    expect(externalHref(`${ORIGIN}/chats/2`, ORIGIN)).toBeNull();
    expect(externalHref('#section', ORIGIN)).toBeNull();
    expect(externalHref('javascript:alert(1)', ORIGIN)).toBeNull();
    expect(externalHref('file:///C:/Windows', ORIGIN)).toBeNull();
    expect(externalHref('cognitrix://pair', ORIGIN)).toBeNull();
    expect(externalHref('', ORIGIN)).toBeNull();
    expect(externalHref(null, ORIGIN)).toBeNull();
  });
});

describe('installExternalLinkHandler', () => {
  let remove = () => {};
  afterEach(() => {
    remove();
    document.body.innerHTML = '';
    openExternal.mockClear();
    desktopMode.on = true;
  });

  const click = (el: Element) => {
    const e = new MouseEvent('click', { bubbles: true, cancelable: true });
    el.dispatchEvent(e);
    return e;
  };

  it('opens outside links in the default browser in the desktop app', () => {
    remove = installExternalLinkHandler();
    document.body.innerHTML = '<p><a href="https://github.com/theonlyamos/cognitrix" target="_blank"><strong>repo</strong></a></p>';
    const e = click(document.querySelector('strong')!);
    expect(e.defaultPrevented).toBe(true);
    expect(openExternal).toHaveBeenCalledWith('https://github.com/theonlyamos/cognitrix');
  });

  it('lets in-app links route normally', () => {
    remove = installExternalLinkHandler();
    document.body.innerHTML = '<a href="/tasks/7">run</a>';
    const e = click(document.querySelector('a')!);
    expect(e.defaultPrevented).toBe(false);
    expect(openExternal).not.toHaveBeenCalled();
  });

  it('does nothing on the web, where links open in a new tab', () => {
    desktopMode.on = false;
    remove = installExternalLinkHandler();
    document.body.innerHTML = '<a href="https://example.com" target="_blank">x</a>';
    const e = click(document.querySelector('a')!);
    expect(e.defaultPrevented).toBe(false);
    expect(openExternal).not.toHaveBeenCalled();
  });
});
