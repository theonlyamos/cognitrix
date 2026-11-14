import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  allThemes, applyVariables, artworkFor, fetchAccountTheme, removeTheme, saveArtwork, saveSelection, saveTheme as saveThemeRemote,
  storeMode, storeSelection, storedMode, storedSelection, themeFor, variablesFor,
  type AccountTheme, type ThemeMode, type ThemeSelection,
} from '@/lib/theme/store';
import { artworkSvg, parseArtwork, DEFAULT_STRENGTH, type Artwork } from '@/lib/theme/artwork';
import { parseThemeFile, themeTokens, themeVariables, type Appearance, type ThemeFile, type TokenValues } from '@/lib/theme/themes';

type Theme = Appearance;
export type { ThemeMode };

/** What a Set Theme result says Undo should put back. */
export interface ThemeUndo {
  mode?: string;
  light?: string;
  dark?: string;
  /** A theme definition the change overwrote. */
  theme?: unknown;
  /** A theme the change added. */
  created?: string;
  /** Artwork choices the change replaced, by theme id (null = the theme's own). */
  artwork?: Record<string, unknown>;
}

/** The drawing on screen now, ready to paint with artworkImage(). */
export interface ShownArtwork {
  svg: string;
  name: string;
  /** 0-1 */
  strength: number;
  tokens: TokenValues;
  /** The theme it belongs to. */
  themeId: string;
}

/** A theme being edited: shown on screen in one appearance until the editor closes. */
export interface ThemePreview { theme: ThemeFile; appearance: Appearance }

interface ThemeCtx {
  /** The appearance on screen right now. */
  theme: Theme;
  mode: ThemeMode;
  toggle: () => void;
  setTheme: (t: Theme) => void;
  setMode: (m: ThemeMode) => void;
  /** Which theme is used for light and for dark, and the person's own themes. */
  selection: ThemeSelection;
  /** Built-ins, then the person's own. */
  themes: ThemeFile[];
  /** Use a theme for one appearance. */
  pickTheme: (appearance: Appearance, id: string) => Promise<void>;
  /** Add or replace one of the person's themes (saved to the account when signed in). */
  saveTheme: (theme: ThemeFile) => Promise<void>;
  deleteTheme: (id: string) => Promise<void>;
  /** Choose a theme's artwork (null = the theme's own). */
  setArtwork: (themeId: string, artwork: Artwork | null) => Promise<void>;
  /** The drawing on screen now, or null. */
  artwork: ShownArtwork | null;
  /** Take the account's copy (after sign-in, on focus, after an agent changed it). */
  refresh: () => Promise<void>;
  /** Put the colours an agent's change replaced back. */
  restore: (previous: ThemeUndo) => Promise<void>;
  preview: ThemePreview | null;
  setPreview: (p: ThemePreview | null) => void;
  /** Whether changes go to the account (signed in) or stay on this device. */
  synced: boolean;
  /** Signed out: keep changes on this device. */
  detach: () => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

/** Read the theme the pre-paint boot script already applied to <html>. */
function currentTheme(): Theme {
  return typeof document !== 'undefined' && document.documentElement.classList.contains('dark')
    ? 'dark'
    : 'light';
}

const systemDark = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-color-scheme: dark)').matches;
const resolve = (m: ThemeMode): Theme => (m === 'system' ? (systemDark() ? 'dark' : 'light') : m);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(currentTheme);
  const [mode, setModeState] = useState<ThemeMode>(storedMode);
  const [selection, setSelection] = useState<ThemeSelection>(storedSelection);
  const [preview, setPreview] = useState<ThemePreview | null>(null);
  const [synced, setSynced] = useState(false);
  const syncedRef = useRef(false);
  // The latest selection, so back-to-back changes (save, then pick light, then
  // pick dark) each build on the one before instead of a stale render's copy.
  const selectionRef = useRef(selection);
  // This device's own mode before the account's mode took over, for Undo.
  const deviceModeRef = useRef<ThemeMode>(mode);
  const modeRef = useRef<ThemeMode>(mode);
  const accountModeRef = useRef<'' | ThemeMode>('');

  // Paint: the appearance class, then that appearance's theme variables
  // (or the theme being edited, in the appearance being edited).
  useEffect(() => {
    const shown = preview ? preview.appearance : theme;
    document.documentElement.classList.toggle('dark', shown === 'dark');
    applyVariables(preview ? themeVariables(preview.theme, preview.appearance) : variablesFor(selection, theme));
  }, [theme, selection, preview]);

  const adopt = useCallback((next: ThemeSelection) => {
    selectionRef.current = next;
    setSelection(next);
    storeSelection(next);
  }, []);

  const applyMode = useCallback((m: ThemeMode) => {
    modeRef.current = m;
    storeMode(m);
    setModeState(m);
    setThemeState(resolve(m));
  }, []);

  /** Take the account's state. Its mode wins only when it has one. */
  const take = useCallback((account: AccountTheme) => {
    syncedRef.current = true;
    setSynced(true);
    adopt({ light: account.light, dark: account.dark, themes: account.themes, artwork: account.artwork });
    if (!accountModeRef.current && account.mode) deviceModeRef.current = modeRef.current;
    accountModeRef.current = account.mode;
    if (account.mode) applyMode(account.mode);
  }, [adopt, applyMode]);

  const refresh = useCallback(async () => {
    try {
      take(await fetchAccountTheme());
    } catch {
      syncedRef.current = false;
      setSynced(false);
    }
  }, [take]);

  const detach = useCallback(() => {
    syncedRef.current = false;
    setSynced(false);
  }, []);

  const setMode = useCallback((m: ThemeMode) => {
    applyMode(m);
    if (syncedRef.current) {
      accountModeRef.current = m;
      void saveSelection({ mode: m }).catch(() => undefined);
    }
  }, [applyMode]);

  const setTheme = useCallback((t: Theme) => setMode(t), [setMode]);
  const toggle = useCallback(() => setMode(currentTheme() === 'dark' ? 'light' : 'dark'), [setMode]);

  const pickTheme = useCallback(async (appearance: Appearance, id: string) => {
    const before = selectionRef.current;
    adopt({ ...before, [appearance]: id });
    if (!syncedRef.current) return;
    try {
      take(await saveSelection({ [appearance]: id }));
    } catch (e) {
      adopt(before);
      throw e;
    }
  }, [adopt, take]);

  const saveTheme = useCallback(async (file: ThemeFile) => {
    if (syncedRef.current) {
      take(await saveThemeRemote(file));
      return;
    }
    const current = selectionRef.current;
    adopt({ ...current, themes: [...current.themes.filter((t) => t.id !== file.id), file] });
  }, [adopt, take]);

  const deleteTheme = useCallback(async (id: string) => {
    if (syncedRef.current) {
      take(await removeTheme(id));
      return;
    }
    const fallback = (picked: string) => (picked === id ? 'signal' : picked);
    const current = selectionRef.current;
    const artwork = { ...current.artwork };
    delete artwork[id];
    adopt({ light: fallback(current.light), dark: fallback(current.dark), themes: current.themes.filter((t) => t.id !== id), artwork });
  }, [adopt, take]);

  const setArtwork = useCallback(async (themeId: string, art: Artwork | null) => {
    if (syncedRef.current) {
      take(await saveArtwork(themeId, art));
      return;
    }
    const current = selectionRef.current;
    const artwork = { ...current.artwork };
    if (art) artwork[themeId] = art;
    else delete artwork[themeId];
    adopt({ ...current, artwork });
  }, [adopt, take]);

  const restore = useCallback(async (previous: ThemeUndo) => {
    // Put back artwork the change replaced.
    for (const [themeId, raw] of Object.entries(previous.artwork ?? {})) {
      let art: Artwork | null = null;
      try { art = parseArtwork(raw) ?? null; } catch { art = null; }
      try { take(await saveArtwork(themeId, art)); } catch { /* keep going */ }
    }
    // Put back a theme definition the change overwrote, or remove one it added.
    if (previous.theme) {
      try { take(await saveThemeRemote(parseThemeFile(previous.theme))); } catch { /* keep going: picks still restore */ }
    }
    const mode = previous.mode === 'light' || previous.mode === 'dark' || previous.mode === 'system' ? previous.mode
      : previous.mode === '' ? '' : undefined;
    const account = await saveSelection({ light: previous.light, dark: previous.dark, ...(mode !== undefined ? { mode } : {}) });
    if (previous.created && previous.created !== previous.light && previous.created !== previous.dark) {
      take(await removeTheme(previous.created));
    } else {
      take(account);
    }
    // The account had no mode of its own: give this device back the mode it had.
    if (mode === '') applyMode(deviceModeRef.current);
  }, [take, applyMode]);

  // Following the system: switch when the OS does.
  useEffect(() => {
    if (mode !== 'system' || !window.matchMedia) return;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => setThemeState(query.matches ? 'dark' : 'light');
    query.addEventListener?.('change', onChange);
    return () => query.removeEventListener?.('change', onChange);
  }, [mode]);

  // Another tab on this device changed the theme.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === 'cognitrix:theme:v1') {
        selectionRef.current = storedSelection();
        setSelection(selectionRef.current);
      }
      if (e.key === null || e.key === 'darkMode') {
        const m = storedMode();
        modeRef.current = m;
        setModeState(m);
        setThemeState(resolve(m));
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const themes = useMemo(() => allThemes(selection), [selection]);

  const artwork = useMemo<ShownArtwork | null>(() => {
    const appearance = preview ? preview.appearance : theme;
    const shown = preview ? preview.theme : themeFor(selection, appearance);
    const art = preview ? (selection.artwork[preview.theme.id] ?? preview.theme.artwork ?? null) : artworkFor(selection, shown);
    const svg = artworkSvg(art);
    if (!svg || !art) return null;
    return {
      svg,
      name: art.svg ? art.name || 'Your drawing' : art.id ?? '',
      strength: (art.strength ?? DEFAULT_STRENGTH) / 100,
      tokens: themeTokens(shown, appearance),
      themeId: shown.id,
    };
  }, [preview, theme, selection]);

  return (
    <Ctx.Provider value={{
      theme, mode, toggle, setTheme, setMode, selection, themes, pickTheme, saveTheme, deleteTheme, setArtwork, artwork,
      refresh, restore, preview, setPreview, synced, detach,
    }}>
      {children}
    </Ctx.Provider>
  );
}

/** The theme context, or null outside ThemeProvider (decorations that can do without). */
export function useOptionalTheme(): ThemeCtx | null {
  return useContext(Ctx);
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}

/**
 * Keeps the theme in step with the account while signed in: on sign-in and
 * whenever the window comes back into view (another device or an agent may
 * have changed it). Rendered inside UserProvider.
 */
export function AccountThemeSync({ signedIn }: { signedIn: boolean }) {
  const { refresh, detach } = useTheme();
  useEffect(() => {
    if (!signedIn) {
      detach();
      return;
    }
    void refresh();
    const onVisible = () => { if (document.visibilityState === 'visible') void refresh(); };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [signedIn, refresh, detach]);
  return null;
}
