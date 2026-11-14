import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { parseThemeFile } from '@/lib/theme/themes';
import { ThemeProvider, useTheme } from './ThemeContext';

type Ctx = ReturnType<typeof useTheme>;

function mount() {
  const ref: { current: Ctx | null } = { current: null };
  function Probe() { ref.current = useTheme(); return null; }
  render(<ThemeProvider><Probe /></ThemeProvider>);
  return ref;
}

const dusk = parseThemeFile({
  id: 'dusk', name: 'Dusk',
  light: { background: '#fbf7f2', text: '#2a2238', accent: '#d9480f' },
  dark: { background: '#15121f', text: '#ece6ff', accent: '#ff8a5c' },
});

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe('ThemeProvider', () => {
  it('keeps a saved theme and both picks when saving and using it on this device only', async () => {
    const ctx = mount();
    await act(async () => {
      await ctx.current!.saveTheme(dusk);
      await ctx.current!.pickTheme('light', 'dusk');
      await ctx.current!.pickTheme('dark', 'dusk');
    });
    expect(ctx.current!.selection.themes.map((t) => t.id)).toEqual(['dusk']);
    expect(ctx.current!.selection.light).toBe('dusk');
    expect(ctx.current!.selection.dark).toBe('dusk');
  });

  it('undo of an agent change puts back an unset account mode and this device\'s own mode', async () => {
    localStorage.setItem('darkMode', 'false'); // this device: light
    const account = { mode: '', light: 'signal', dark: 'signal', themes: [] as unknown[] };
    vi.spyOn(api, 'get').mockImplementation(async () => ({ data: { ...account } }));
    const put = vi.spyOn(api, 'put').mockImplementation(async (_url, body) => {
      Object.assign(account, body as object);
      return { data: { ...account } };
    });
    const ctx = mount();
    await act(async () => { await ctx.current!.refresh(); });
    expect(ctx.current!.mode).toBe('light');

    // The agent switched the account to dark Nord.
    Object.assign(account, { mode: 'dark', light: 'nord', dark: 'nord' });
    await act(async () => { await ctx.current!.refresh(); });
    expect(ctx.current!.mode).toBe('dark');

    await act(async () => { await ctx.current!.restore({ mode: '', light: 'signal', dark: 'signal' }); });
    expect(put).toHaveBeenCalledWith('/theme', { light: 'signal', dark: 'signal', mode: '' });
    expect(ctx.current!.mode).toBe('light');
    expect(ctx.current!.selection.dark).toBe('signal');
  });

  it('undo puts back a theme the agent overwrote and removes one it added', async () => {
    const account = { mode: 'dark', light: 'signal', dark: 'forest', themes: [] as unknown[] };
    vi.spyOn(api, 'get').mockImplementation(async () => ({ data: { ...account } }));
    const put = vi.spyOn(api, 'put').mockImplementation(async (url, body) => {
      if (url === '/theme') Object.assign(account, body as object);
      else account.themes = [body];
      return { data: { ...account } };
    });
    const del = vi.spyOn(api, 'delete').mockImplementation(async () => {
      account.themes = [];
      return { data: { ...account } };
    });
    const ctx = mount();
    await act(async () => { await ctx.current!.refresh(); });

    await act(async () => { await ctx.current!.restore({ mode: 'dark', light: 'signal', dark: 'dusk', theme: dusk }); });
    expect(put).toHaveBeenCalledWith('/theme/themes/dusk', dusk);
    expect(del).not.toHaveBeenCalled();

    await act(async () => { await ctx.current!.restore({ mode: 'dark', light: 'signal', dark: 'signal', created: 'forest' }); });
    expect(del).toHaveBeenCalledWith('/theme/themes/forest');
  });

  it('shows the theme\'s own artwork, the person\'s choice over it, and none when turned off', async () => {
    document.documentElement.classList.add('dark');
    const ctx = mount();
    expect(ctx.current!.artwork?.name).toBe('signal');
    await act(async () => { await ctx.current!.pickTheme('dark', 'nord'); });
    expect(ctx.current!.artwork?.name).toBe('aurora');
    await act(async () => { await ctx.current!.setArtwork('nord', { svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><circle r="1" fill="accent"/></svg>', name: 'Dot', strength: 50 }); });
    expect(ctx.current!.artwork).toMatchObject({ name: 'Dot', strength: 0.5, themeId: 'nord' });
    await act(async () => { await ctx.current!.setArtwork('nord', { id: 'none' }); });
    expect(ctx.current!.artwork).toBeNull();
    document.documentElement.classList.remove('dark');
  });

  it('undo puts back the artwork an agent replaced', async () => {
    const account = { mode: '', light: 'signal', dark: 'nord', themes: [] as unknown[], artwork: { nord: { id: 'skyline' } } as Record<string, unknown> };
    vi.spyOn(api, 'get').mockImplementation(async () => ({ data: { ...account } }));
    vi.spyOn(api, 'put').mockImplementation(async (url, body) => {
      if (String(url).startsWith('/theme/artwork/')) account.artwork = { ...account.artwork, [String(url).split('/').pop()!]: body };
      return { data: { ...account } };
    });
    const del = vi.spyOn(api, 'delete').mockImplementation(async (url) => {
      const artwork = { ...account.artwork };
      delete artwork[String(url).split('/').pop()!];
      account.artwork = artwork;
      return { data: { ...account } };
    });
    const ctx = mount();
    await act(async () => { await ctx.current!.refresh(); });
    await act(async () => { await ctx.current!.restore({ mode: '', light: 'signal', dark: 'nord', artwork: { nord: null, signal: { id: 'blueprint' } } }); });
    expect(del).toHaveBeenCalledWith('/theme/artwork/nord');
    expect(ctx.current!.selection.artwork).toEqual({ signal: { id: 'blueprint' } });
  });
});
