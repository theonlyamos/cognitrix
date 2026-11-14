// Themes: the file format, the built-in presets, and turning a theme into the
// CSS variables app.css defines. A theme only has to name a background, text
// and accent colour per appearance; everything else is derived so a custom
// theme can't end up half-styled or unreadable. Any token can still be set
// by hand under `colors`.
//
// The backend keeps the same shape (cognitrix/themes.py validates it) and the
// built-in ids there must match presets.json (a Python test checks).

import presetsJson from './presets.json';
import {
  contrastRatio, hexToRgb, isHex, mix, normalizeHex, readableOn, rgbChannels, shiftLightness, textOn, luminance,
} from './color';

export type Appearance = 'light' | 'dark';

/** Tokens a theme may set by hand. Each is a `--name` variable in app.css. */
export const TOKENS = [
  'bg', 'panel', 'panel-2', 'line', 'fg', 'fg-dim',
  'accent', 'on-accent', 'accent-ink', 'focus',
  'danger', 'danger-ink', 'ok',
  'warn', 'on-warn', 'warn-ink', 'info', 'info-ink', 'violet', 'violet-ink',
  'syn-num', 'syn-alt',
] as const;
export type TokenName = (typeof TOKENS)[number];
const TOKEN_SET: ReadonlySet<string> = new Set(TOKENS);

/** Tokens with an `-rgb` twin (Tailwind opacity classes such as bg-accent/15). */
const RGB_TWINS: ReadonlySet<TokenName> = new Set([
  'bg', 'panel', 'panel-2', 'fg', 'fg-dim', 'accent', 'on-accent', 'accent-ink',
  'danger', 'danger-ink', 'ok', 'warn', 'on-warn', 'warn-ink', 'info', 'info-ink', 'violet', 'violet-ink',
]);

/** Plain words for the editor's Advanced list. */
export const TOKEN_LABELS: Record<TokenName, string> = {
  bg: 'Background', panel: 'Panels', 'panel-2': 'Raised panels, code', line: 'Borders',
  fg: 'Text', 'fg-dim': 'Secondary text', accent: 'Accent', 'on-accent': 'Text on accent',
  'accent-ink': 'Accent as text, links', focus: 'Focus ring', danger: 'Danger', 'danger-ink': 'Danger text',
  ok: 'Success', warn: 'Warning badges', 'on-warn': 'Text on warning', 'warn-ink': 'Warning text',
  info: 'Info tint', 'info-ink': 'Info text', violet: 'Violet tint', 'violet-ink': 'Violet text',
  'syn-num': 'Code: numbers', 'syn-alt': 'Code: strings',
};

export interface ThemeVariant {
  background: string;
  text: string;
  accent: string;
  colors?: Partial<Record<TokenName, string>>;
}

export interface ThemeFonts { ui?: string | null; code?: string | null }

export interface ThemeFile {
  format: 'cognitrix-theme';
  version: 1;
  id: string;
  name: string;
  light?: ThemeVariant;
  dark?: ThemeVariant;
  /** 0-100, default 50: how far secondary text and borders stand out. */
  contrast?: number;
  fonts?: ThemeFonts;
}

export const THEME_FORMAT = 'cognitrix-theme';
export const DEFAULT_THEME_ID = 'signal';
export const MAX_CUSTOM_THEMES = 50;

export const PRESETS: ThemeFile[] = presetsJson as ThemeFile[];
export const PRESET_IDS: ReadonlySet<string> = new Set(PRESETS.map((t) => t.id));

export function isPreset(id: string): boolean {
  return PRESET_IDS.has(id);
}

export function themeModes(theme: ThemeFile): Appearance[] {
  return (['light', 'dark'] as const).filter((m) => theme[m]);
}

// ---------------------------------------------------------------- validation

const RGBA = /^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+)\s*)?\)$/i;
const HEX8 = /^#[0-9a-f]{8}$/i;
const FONT = /^[\p{L}\p{N} ._'-]{1,64}$/u;

/** A colour a token may hold: hex, or rgb()/rgba() for translucent borders. */
export function isTokenColor(value: unknown): value is string {
  return typeof value === 'string' && (isHex(value) || HEX8.test((value as string).trim()) || RGBA.test((value as string).trim()));
}

export function slugify(name: string): string {
  const slug = name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  return slug || 'theme';
}

export class ThemeFormatError extends Error {}

function cleanVariant(raw: unknown, where: string): ThemeVariant | undefined {
  if (raw === undefined || raw === null) return undefined;
  if (typeof raw !== 'object' || Array.isArray(raw)) throw new ThemeFormatError(`"${where}" must be an object.`);
  const v = raw as Record<string, unknown>;
  const out: ThemeVariant = { background: '', text: '', accent: '' };
  for (const key of ['background', 'text', 'accent'] as const) {
    const hex = normalizeHex(v[key]);
    if (!hex) throw new ThemeFormatError(`"${where}.${key}" must be a hex colour such as #1a1b26.`);
    out[key] = hex;
  }
  if (v.colors !== undefined) {
    if (typeof v.colors !== 'object' || v.colors === null || Array.isArray(v.colors)) {
      throw new ThemeFormatError(`"${where}.colors" must be an object.`);
    }
    const colors: Partial<Record<TokenName, string>> = {};
    for (const [key, value] of Object.entries(v.colors as Record<string, unknown>)) {
      if (!TOKEN_SET.has(key)) continue; // unknown names from newer versions: ignore
      if (!isTokenColor(value)) throw new ThemeFormatError(`"${where}.colors.${key}" must be a colour.`);
      colors[key as TokenName] = normalizeHex(value) ?? value.trim();
    }
    if (Object.keys(colors).length) out.colors = colors;
  }
  return out;
}

function cleanFont(value: unknown, where: string): string | null {
  if (value === undefined || value === null || value === '') return null;
  if (typeof value !== 'string' || !FONT.test(value.trim())) {
    throw new ThemeFormatError(`"${where}" must be a font name (letters, numbers, spaces).`);
  }
  return value.trim();
}

/** Check and tidy a theme file. Throws ThemeFormatError with a readable reason. */
export function parseThemeFile(raw: unknown): ThemeFile {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw new ThemeFormatError('A theme must be a JSON object.');
  const t = raw as Record<string, unknown>;
  if (t.format !== undefined && t.format !== THEME_FORMAT) throw new ThemeFormatError('This is not a Cognitrix theme.');
  if (t.version !== undefined && t.version !== 1) throw new ThemeFormatError('This theme was made by a newer version of Cognitrix.');
  const name = typeof t.name === 'string' ? t.name.trim().slice(0, 48) : '';
  if (!name) throw new ThemeFormatError('The theme needs a name.');
  const light = cleanVariant(t.light, 'light');
  const dark = cleanVariant(t.dark, 'dark');
  if (!light && !dark) throw new ThemeFormatError('The theme needs a "light" or "dark" variant (or both).');
  const id = typeof t.id === 'string' && /^[a-z0-9][a-z0-9-]{0,47}$/.test(t.id) ? t.id : slugify(name);
  const out: ThemeFile = { format: THEME_FORMAT, version: 1, id, name };
  if (light) out.light = light;
  if (dark) out.dark = dark;
  if (t.contrast !== undefined && t.contrast !== null) {
    const c = Number(t.contrast);
    if (!Number.isFinite(c)) throw new ThemeFormatError('"contrast" must be a number from 0 to 100.');
    out.contrast = Math.round(Math.min(100, Math.max(0, c)));
  }
  if (t.fonts !== undefined && t.fonts !== null) {
    if (typeof t.fonts !== 'object' || Array.isArray(t.fonts)) throw new ThemeFormatError('"fonts" must be an object.');
    const f = t.fonts as Record<string, unknown>;
    const ui = cleanFont(f.ui, 'fonts.ui');
    const code = cleanFont(f.code, 'fonts.code');
    if (ui || code) out.fonts = { ui, code };
  }
  return out;
}

// ---------------------------------------------------------------- derivation

export type TokenValues = Record<TokenName, string>;

const DEFAULT_SEMANTIC: Record<Appearance, Record<'danger' | 'ok' | 'warn-ink' | 'info-ink' | 'violet-ink' | 'syn-num' | 'syn-alt', string>> = {
  light: { danger: '#c23528', ok: '#3f7d20', 'warn-ink': '#a15c00', 'info-ink': '#1f6f8b', 'violet-ink': '#6b46c1', 'syn-num': '#a15c00', 'syn-alt': '#1f6f8b' },
  dark: { danger: '#ff6b5e', ok: '#9fe870', 'warn-ink': '#ffb86b', 'info-ink': '#7cc4ff', 'violet-ink': '#c9b0ff', 'syn-num': '#e5b567', 'syn-alt': '#6fb3d2' },
};

/**
 * Every token for one appearance of a theme. Panels step away from the
 * background, secondary text and borders mix text into the background (more
 * so as `contrast` rises), and coloured text is nudged until it reads.
 */
export function deriveTokens(variant: ThemeVariant, appearance: Appearance, contrast = 50): TokenValues {
  const k = Math.min(100, Math.max(0, contrast)) / 100;
  const dark = appearance === 'dark';
  const o = variant.colors ?? {};
  const pick = (name: TokenName, derived: () => string) => o[name] ?? derived();

  const bg = o.bg ?? variant.background;
  const fg = o.fg ?? variant.text;
  const accent = o.accent ?? variant.accent;
  const panel = pick('panel', () => (dark ? shiftLightness(bg, 0.035) : shiftLightness(bg, 0.025)));
  const panel2 = pick('panel-2', () => (dark ? shiftLightness(bg, 0.075) : shiftLightness(bg, -0.03)));
  const surfaces = [bg, ...[panel, panel2].filter((c) => isHex(c))];
  const { r, g, b } = hexToRgb(fg);
  const lineAlpha = (dark ? 0.05 + 0.08 * k : 0.06 + 0.1 * k).toFixed(3).replace(/0+$/, '');
  const line = pick('line', () => `rgba(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)},${lineAlpha})`);
  const fgDim = pick('fg-dim', () => readableOn(mix(fg, bg, 0.5 - 0.3 * k), surfaces, 3.5 + 2 * k));
  const semantic = DEFAULT_SEMANTIC[appearance];
  const ink = (name: keyof typeof semantic, target: number) => pick(name, () => readableOn(semantic[name], surfaces, target));
  const warnInk = ink('warn-ink', 4.5);
  const danger = ink('danger', 4.5);

  return {
    bg,
    panel,
    'panel-2': panel2,
    line,
    fg,
    'fg-dim': fgDim,
    accent,
    'on-accent': pick('on-accent', () => textOn(accent)),
    'accent-ink': pick('accent-ink', () => readableOn(accent, surfaces, 4.5)),
    focus: pick('focus', () => readableOn(accent, surfaces, 3)),
    danger,
    'danger-ink': pick('danger-ink', () => danger),
    ok: ink('ok', 3),
    warn: pick('warn', () => '#ffb86b'),
    'on-warn': pick('on-warn', () => textOn(o.warn ?? '#ffb86b')),
    'warn-ink': warnInk,
    info: pick('info', () => '#7cc4ff'),
    'info-ink': ink('info-ink', 4.5),
    violet: pick('violet', () => '#b48cff'),
    'violet-ink': ink('violet-ink', 4.5),
    'syn-num': ink('syn-num', 4.5),
    'syn-alt': ink('syn-alt', 4.5),
  };
}

export const DEFAULT_FONT_UI = "'Space Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif";
export const DEFAULT_FONT_CODE = "'JetBrains Mono', ui-monospace, monospace";

/** The CSS variables to write on <html> for a theme in one appearance. */
export function themeVariables(theme: ThemeFile, appearance: Appearance): Record<string, string> {
  const variant = theme[appearance] ?? theme.light ?? theme.dark!;
  const used: Appearance = theme[appearance] ? appearance : theme.light ? 'light' : 'dark';
  const tokens = deriveTokens(variant, used, theme.contrast ?? 50);
  const vars: Record<string, string> = {};
  for (const name of TOKENS) {
    vars[`--${name}`] = tokens[name];
    if (RGB_TWINS.has(name) && isHex(tokens[name])) vars[`--${name}-rgb`] = rgbChannels(tokens[name]);
  }
  vars['--font-ui'] = theme.fonts?.ui ? `'${theme.fonts.ui}', ${DEFAULT_FONT_UI}` : DEFAULT_FONT_UI;
  vars['--font-code'] = theme.fonts?.code ? `'${theme.fonts.code}', ${DEFAULT_FONT_CODE}` : DEFAULT_FONT_CODE;
  return vars;
}

/** Pairs a reader should be able to read, with how well they do. For warnings in the editor. */
export function contrastWarnings(tokens: TokenValues): string[] {
  const out: string[] = [];
  const surfaces = [tokens.bg, tokens.panel, tokens['panel-2']].filter((c) => isHex(c));
  const check = (label: string, color: string, target: number) => {
    if (!isHex(color)) return;
    const worst = Math.min(...surfaces.map((s) => contrastRatio(color, s)));
    if (worst < target) out.push(`${label} is hard to read (${worst.toFixed(1)}:1, aim for ${target}:1).`);
  };
  check('Text', tokens.fg, 4.5);
  check('Secondary text', tokens['fg-dim'], 4.5);
  check('Accent as text', tokens['accent-ink'], 4.5);
  check('Focus ring', tokens.focus, 3);
  if (isHex(tokens.accent) && isHex(tokens['on-accent']) && contrastRatio(tokens.accent, tokens['on-accent']) < 4.5) {
    out.push('Text on accent buttons is hard to read.');
  }
  return out;
}

export function isDarkColor(hex: string): boolean {
  return luminance(hex) < 0.18;
}

// ---------------------------------------------------------------- import / export

export function exportTheme(theme: ThemeFile): string {
  return JSON.stringify(theme, null, 2);
}

/** Read JSON with // and /* comments and trailing commas (VS Code theme files have them). */
function parseLooseJson(text: string): unknown {
  let out = '';
  let inString = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      out += ch;
      if (ch === '\\') { out += text[++i] ?? ''; continue; }
      if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') { inString = true; out += ch; continue; }
    if (ch === '/' && text[i + 1] === '/') { while (i < text.length && text[i] !== '\n') i++; continue; }
    if (ch === '/' && text[i + 1] === '*') { i = text.indexOf('*/', i + 2); if (i < 0) break; i++; continue; }
    out += ch;
  }
  return JSON.parse(out.replace(/,(\s*[}\]])/g, '$1'));
}

function opaque(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const v = value.trim();
  if (HEX8.test(v)) return normalizeHex(v.slice(0, 7));
  return normalizeHex(v);
}

/** A VS Code colour theme (`colors` with workbench keys) → one variant. */
function fromVsCode(raw: Record<string, unknown>, fallbackName: string): ThemeFile {
  const c = raw.colors as Record<string, unknown>;
  const first = (...keys: string[]) => keys.map((k) => opaque(c[k])).find(Boolean) ?? null;
  const background = first('editor.background', 'sideBar.background', 'panel.background');
  const text = first('editor.foreground', 'foreground', 'sideBar.foreground');
  if (!background || !text) throw new ThemeFormatError('This VS Code theme has no editor background and foreground colours.');
  const accent = first('button.background', 'focusBorder', 'activityBarBadge.background', 'textLink.foreground', 'badge.background') ?? text;
  const typeField = String(raw.type ?? '').toLowerCase();
  const appearance: Appearance = typeField === 'light' || typeField === 'hc-light' ? 'light'
    : typeField === 'dark' || typeField === 'hc-black' ? 'dark'
      : isDarkColor(background) ? 'dark' : 'light';
  const colors: Partial<Record<TokenName, string>> = {};
  const set = (token: TokenName, ...keys: string[]) => { const v = first(...keys); if (v) colors[token] = v; };
  set('panel', 'sideBar.background', 'activityBar.background');
  set('panel-2', 'input.background', 'dropdown.background', 'editorWidget.background');
  set('fg-dim', 'descriptionForeground');
  set('on-accent', 'button.foreground');
  set('danger', 'errorForeground', 'editorError.foreground');
  set('ok', 'terminal.ansiGreen', 'gitDecoration.addedResourceForeground');
  // Drop picks that would not read; the derivation fills them in instead.
  if (colors['fg-dim'] && contrastRatio(colors['fg-dim'], background) < 3.5) delete colors['fg-dim'];
  if (colors['on-accent'] && contrastRatio(colors['on-accent'], accent) < 4.5) delete colors['on-accent'];
  const name = typeof raw.name === 'string' && raw.name.trim() ? raw.name.trim() : fallbackName;
  const variant: ThemeVariant = { background, text, accent, ...(Object.keys(colors).length ? { colors } : {}) };
  return parseThemeFile({ format: THEME_FORMAT, version: 1, name, [appearance]: variant });
}

/** A Codex app theme string (`codex-theme-v1:{...}`) → one variant. */
function fromCodex(text: string, fallbackName: string): ThemeFile {
  let raw: Record<string, unknown>;
  try {
    raw = JSON.parse(text.slice(text.indexOf(':') + 1).trim()) as Record<string, unknown>;
  } catch {
    throw new ThemeFormatError('This Codex theme string is not valid JSON after "codex-theme-v1:".');
  }
  const t = (raw.theme ?? {}) as Record<string, unknown>;
  const appearance: Appearance = raw.variant === 'light' ? 'light' : 'dark';
  const fonts = (t.fonts ?? {}) as Record<string, unknown>;
  const semantic = (t.semanticColors ?? {}) as Record<string, unknown>;
  const colors: Partial<Record<TokenName, string>> = {};
  const added = opaque(semantic.diffAdded);
  const removed = opaque(semantic.diffRemoved);
  if (added) colors.ok = added;
  if (removed) colors.danger = removed;
  return parseThemeFile({
    format: THEME_FORMAT, version: 1,
    name: typeof raw.name === 'string' && raw.name.trim() ? raw.name : fallbackName,
    contrast: typeof t.contrast === 'number' ? t.contrast : undefined,
    fonts: { ui: typeof fonts.ui === 'string' ? fonts.ui : null, code: typeof fonts.code === 'string' ? fonts.code : null },
    [appearance]: { background: t.surface, text: t.ink, accent: t.accent, ...(Object.keys(colors).length ? { colors } : {}) },
  });
}

/**
 * Pasted or dropped text → a theme. Accepts a Cognitrix theme, a Codex app
 * theme string, or a VS Code colour theme JSON.
 */
export function importTheme(text: string, fallbackName = 'Imported theme'): ThemeFile {
  const trimmed = text.trim();
  if (!trimmed) throw new ThemeFormatError('Paste a theme first.');
  if (trimmed.length > 512 * 1024) throw new ThemeFormatError('That file is too large to be a theme.');
  if (/^codex-theme-v\d+:/i.test(trimmed)) return fromCodex(trimmed, fallbackName);
  let raw: unknown;
  try {
    raw = parseLooseJson(trimmed);
  } catch {
    throw new ThemeFormatError('This is not a theme: it should be JSON, or a codex-theme-v1: string.');
  }
  if (typeof raw === 'object' && raw !== null && !Array.isArray(raw)) {
    const obj = raw as Record<string, unknown>;
    if (obj.format === THEME_FORMAT || obj.light || obj.dark) return parseThemeFile(obj);
    if (typeof obj.colors === 'object' && obj.colors !== null) return fromVsCode(obj, fallbackName);
  }
  throw new ThemeFormatError('This JSON is not a Cognitrix, Codex or VS Code theme.');
}

/** A theme to start editing from: a copy of `base` with a fresh id and name. */
export function duplicateTheme(base: ThemeFile, taken: Iterable<string>, name?: string): ThemeFile {
  const copy = JSON.parse(JSON.stringify(base)) as ThemeFile;
  copy.name = name ?? `${base.name} copy`;
  copy.id = uniqueId(slugify(copy.name), taken);
  return copy;
}

export function uniqueId(wanted: string, taken: Iterable<string>): string {
  const used = new Set([...taken, ...PRESET_IDS]);
  if (!used.has(wanted)) return wanted;
  for (let i = 2; ; i++) {
    const candidate = `${wanted.slice(0, 44)}-${i}`;
    if (!used.has(candidate)) return candidate;
  }
}
