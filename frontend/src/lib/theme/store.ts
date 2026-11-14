// Where themes live on this device and on the account.
//
// The account (GET /api/v1/theme) is the source of truth so the web and
// desktop apps match and agents can change the theme (Set Theme tool). This
// device keeps a copy so the app paints in the right colours before it has
// signed in: public/theme.js reads VARS_KEY before first paint.

import { api } from '@/lib/api';
import {
  DEFAULT_THEME_ID, PRESETS, isPreset, parseThemeFile, themeVariables,
  type Appearance, type ThemeFile,
} from './themes';

export type ThemeMode = Appearance | 'system';

export interface ThemeSelection {
  light: string;
  dark: string;
  themes: ThemeFile[];
}

/** What GET /theme returns. `mode` '' = each device keeps its own. */
export interface AccountTheme extends ThemeSelection {
  mode: '' | ThemeMode;
}

export const STATE_KEY = 'cognitrix:theme:v1';
/** { light: {--var: value}, dark: {...} } for public/theme.js. Empty = Signal (app.css). */
export const VARS_KEY = 'cognitrix:theme-vars:v1';
/** Kept from before themes: 'true' / 'false' / absent (= follow the system). */
export const MODE_KEY = 'darkMode';

function read(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* storage blocked: lasts for this page only */ }
}

export function storedMode(): ThemeMode {
  const saved = read(MODE_KEY);
  return saved === 'true' ? 'dark' : saved === 'false' ? 'light' : 'system';
}

export function storeMode(mode: ThemeMode): void {
  write(MODE_KEY, mode === 'system' ? null : String(mode === 'dark'));
}

const DEFAULT_SELECTION: ThemeSelection = { light: DEFAULT_THEME_ID, dark: DEFAULT_THEME_ID, themes: [] };

function cleanThemes(raw: unknown): ThemeFile[] {
  if (!Array.isArray(raw)) return [];
  const out: ThemeFile[] = [];
  for (const item of raw) {
    try { out.push(parseThemeFile(item)); } catch { /* skip a broken entry */ }
  }
  return out;
}

export function storedSelection(): ThemeSelection {
  try {
    const raw = JSON.parse(read(STATE_KEY) ?? 'null') as Partial<ThemeSelection> | null;
    if (!raw) return DEFAULT_SELECTION;
    return {
      light: typeof raw.light === 'string' ? raw.light : DEFAULT_THEME_ID,
      dark: typeof raw.dark === 'string' ? raw.dark : DEFAULT_THEME_ID,
      themes: cleanThemes(raw.themes),
    };
  } catch {
    return DEFAULT_SELECTION;
  }
}

/** Every theme the person can pick: built-ins first, then their own. */
export function allThemes(selection: ThemeSelection): ThemeFile[] {
  return [...PRESETS, ...selection.themes.filter((t) => !isPreset(t.id))];
}

export function findTheme(selection: ThemeSelection, id: string): ThemeFile | undefined {
  return allThemes(selection).find((t) => t.id === id);
}

/** The theme in use for an appearance, falling back to Signal. */
export function themeFor(selection: ThemeSelection, appearance: Appearance): ThemeFile {
  const picked = findTheme(selection, selection[appearance]);
  if (picked && picked[appearance]) return picked;
  return PRESETS[0];
}

export function variablesFor(selection: ThemeSelection, appearance: Appearance): Record<string, string> {
  const theme = themeFor(selection, appearance);
  return theme.id === DEFAULT_THEME_ID ? {} : themeVariables(theme, appearance);
}

/** Save the selection and the variables theme.js applies before first paint. */
export function storeSelection(selection: ThemeSelection): void {
  write(STATE_KEY, JSON.stringify(selection));
  const vars = { light: variablesFor(selection, 'light'), dark: variablesFor(selection, 'dark') };
  write(VARS_KEY, Object.keys(vars.light).length || Object.keys(vars.dark).length ? JSON.stringify(vars) : null);
}

// ---------------------------------------------------------------- applying

let applied: string[] = [];

/** Write a theme's variables on <html> (or clear them for Signal, whose values are app.css). */
export function applyVariables(vars: Record<string, string>): void {
  const style = document.documentElement.style;
  for (const name of applied) if (!(name in vars)) style.removeProperty(name);
  for (const [name, value] of Object.entries(vars)) style.setProperty(name, value);
  applied = Object.keys(vars);
  const bg = vars['--bg'] ?? getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  if (bg) {
    for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) meta.content = bg;
  }
}

// ---------------------------------------------------------------- account

function fromAccount(data: AccountTheme): AccountTheme {
  return {
    mode: data.mode === 'light' || data.mode === 'dark' || data.mode === 'system' ? data.mode : '',
    light: data.light || DEFAULT_THEME_ID,
    dark: data.dark || DEFAULT_THEME_ID,
    themes: cleanThemes(data.themes),
  };
}

export async function fetchAccountTheme(): Promise<AccountTheme> {
  return fromAccount((await api.get<AccountTheme>('/theme')).data);
}

export async function saveSelection(change: { mode?: ThemeMode; light?: string; dark?: string }): Promise<AccountTheme> {
  return fromAccount((await api.put<AccountTheme>('/theme', change)).data);
}

export async function saveTheme(theme: ThemeFile): Promise<AccountTheme> {
  return fromAccount((await api.put<AccountTheme>(`/theme/themes/${encodeURIComponent(theme.id)}`, theme)).data);
}

export async function removeTheme(id: string): Promise<AccountTheme> {
  return fromAccount((await api.delete<AccountTheme>(`/theme/themes/${encodeURIComponent(id)}`)).data);
}
