import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { contrastRatio, isHex, readableOn } from './color';
import {
  PRESETS, TOKENS, ThemeFormatError, contrastWarnings, deriveTokens, duplicateTheme, importTheme,
  parseThemeFile, themeModes, themeVariables, type Appearance,
} from './themes';

const css = readFileSync(resolve(process.cwd(), 'src/app.css'), 'utf8');
const block = (selector: RegExp) => selector.exec(css)?.[1] ?? '';
const CSS_BLOCKS: Record<Appearance, string> = {
  light: block(/:root\s*\{([\s\S]*?)\}/),
  dark: block(/html\.dark\s*\{([\s\S]*?)\}/),
};
const cssToken = (appearance: Appearance, name: string) =>
  new RegExp(`--${name}:([^;]+);`).exec(CSS_BLOCKS[appearance])?.[1].trim() ?? '';

describe('theme presets', () => {
  it('Signal reproduces app.css exactly, so the default look never changes', () => {
    const signal = PRESETS.find((t) => t.id === 'signal')!;
    for (const appearance of ['light', 'dark'] as const) {
      const vars = themeVariables(signal, appearance);
      for (const name of TOKENS) {
        expect(vars[`--${name}`], `${appearance} --${name}`).toBe(cssToken(appearance, name));
        const twin = cssToken(appearance, `${name}-rgb`);
        if (twin) expect(vars[`--${name}-rgb`], `${appearance} --${name}-rgb`).toBe(twin);
      }
    }
  });

  it('every app.css colour token is themeable', () => {
    for (const appearance of ['light', 'dark'] as const) {
      const names = [...CSS_BLOCKS[appearance].matchAll(/--([a-z0-9-]+):/g)].map((m) => m[1])
        .filter((n) => !n.endsWith('-rgb') && !/^(r\d|ring|font-|fg-\d|bg-\d|accent-color)/.test(n));
      expect(names.filter((n) => !(TOKENS as readonly string[]).includes(n)), appearance).toEqual([]);
    }
  });

  it.each(PRESETS.flatMap((t) => themeModes(t).map((m) => [t.id, m] as const)))(
    '%s (%s) is readable', (id, appearance) => {
      const theme = PRESETS.find((t) => t.id === id)!;
      const tokens = deriveTokens(theme[appearance]!, appearance, theme.contrast ?? 50);
      const surfaces = [tokens.bg, tokens.panel, tokens['panel-2']];
      const worst = (color: string) => Math.min(...surfaces.map((s) => contrastRatio(color, s)));
      expect(worst(tokens.fg)).toBeGreaterThanOrEqual(4.5);
      expect(worst(tokens['fg-dim'])).toBeGreaterThanOrEqual(id === 'signal' ? 3 : 4.5);
      expect(worst(tokens['accent-ink'])).toBeGreaterThanOrEqual(4.5);
      expect(worst(tokens.focus)).toBeGreaterThanOrEqual(3);
      expect(contrastRatio(tokens.accent, tokens['on-accent'])).toBeGreaterThanOrEqual(4.5);
      if (id !== 'signal') expect(contrastWarnings(tokens)).toEqual([]);
    });
});

describe('deriving a theme from three colours', () => {
  it('fills every token with a colour and keeps coloured text readable', () => {
    const tokens = deriveTokens({ background: '#202020', text: '#e0e0e0', accent: '#3050ff' }, 'dark');
    for (const name of TOKENS) expect(tokens[name], name).toBeTruthy();
    expect(tokens.line).toMatch(/^rgba\(/);
    expect(contrastRatio(tokens['accent-ink'], '#202020')).toBeGreaterThanOrEqual(4.5);
    expect(tokens.accent).toBe('#3050ff'); // the fill keeps the picked colour
  });

  it('hand-set tokens win', () => {
    const tokens = deriveTokens({ background: '#ffffff', text: '#111111', accent: '#ff0000', colors: { panel: '#fafafa', 'fg-dim': '#555555' } }, 'light');
    expect(tokens.panel).toBe('#fafafa');
    expect(tokens['fg-dim']).toBe('#555555');
  });

  it('contrast moves secondary text toward the main text', () => {
    const v = { background: '#ffffff', text: '#000000', accent: '#0047ff' };
    const low = deriveTokens(v, 'light', 0)['fg-dim'];
    const high = deriveTokens(v, 'light', 100)['fg-dim'];
    expect(contrastRatio(high, '#ffffff')).toBeGreaterThan(contrastRatio(low, '#ffffff'));
  });

  it('readableOn keeps a colour that already reads and nudges one that does not', () => {
    expect(readableOn('#000000', ['#ffffff'], 4.5)).toBe('#000000');
    const nudged = readableOn('#c6f24e', ['#ffffff'], 4.5);
    expect(isHex(nudged)).toBe(true);
    expect(contrastRatio(nudged, '#ffffff')).toBeGreaterThanOrEqual(4.5);
  });

  it('a one-appearance theme falls back to that appearance', () => {
    const vars = themeVariables(parseThemeFile({ name: 'Dark only', dark: { background: '#000', text: '#fff', accent: '#f80' } }), 'light');
    expect(vars['--bg']).toBe('#000000');
  });

  it('fonts go first, with the defaults after', () => {
    const vars = themeVariables(parseThemeFile({ name: 'F', light: { background: '#fff', text: '#000', accent: '#00f' }, fonts: { code: 'Fira Code' } }), 'light');
    expect(vars['--font-code']).toMatch(/^'Fira Code', 'JetBrains Mono'/);
    expect(vars['--font-ui']).toMatch(/^'Space Grotesk'/);
  });
});

describe('theme files', () => {
  it('parses and tidies', () => {
    const t = parseThemeFile({ name: ' Night ', dark: { background: '#1A1B26', text: 'c0caf5', accent: '#7af', colors: { danger: '#f7768e', mystery: '#000' } }, contrast: 300 });
    expect(t).toEqual({
      format: 'cognitrix-theme', version: 1, id: 'night', name: 'Night', contrast: 100,
      dark: { background: '#1a1b26', text: '#c0caf5', accent: '#77aaff', colors: { danger: '#f7768e' } },
    });
  });

  it.each([
    [{ name: 'x' }, /variant/],
    [{ name: 'x', dark: { background: 'red', text: '#fff', accent: '#fff' } }, /hex/],
    [{ name: 'x', dark: { background: '#000', text: '#fff', accent: '#fff', colors: { fg: 'url(https://evil)' } } }, /colour/],
    [{ name: 'x', light: { background: '#000', text: '#fff', accent: '#fff' }, fonts: { ui: "x'; } body {" } }, /font/],
    [{ name: 'x', version: 2, dark: { background: '#000', text: '#fff', accent: '#fff' } }, /newer/],
  ])('refuses %j', (raw, reason) => {
    expect(() => parseThemeFile(raw)).toThrow(reason);
  });

  it('round-trips through export and import', () => {
    const nord = PRESETS.find((t) => t.id === 'nord')!;
    expect(importTheme(JSON.stringify(nord))).toEqual(nord);
  });

  it('imports a Codex app theme string', () => {
    const t = importTheme('codex-theme-v1:{"codeThemeId":"one","variant":"dark","theme":{"accent":"#5c99d6","contrast":48,"fonts":{"code":"Fira Code","ui":null},"ink":"#d8dee9","opaqueWindows":true,"semanticColors":{"diffAdded":"#99c794","diffRemoved":"#ec5f66","skill":"#c695c6"},"surface":"#303841"}}', 'From Codex');
    expect(t.name).toBe('From Codex');
    expect(t.dark).toEqual({ background: '#303841', text: '#d8dee9', accent: '#5c99d6', colors: { ok: '#99c794', danger: '#ec5f66' } });
    expect(t.light).toBeUndefined();
    expect(t.contrast).toBe(48);
    expect(t.fonts).toEqual({ ui: null, code: 'Fira Code' });
  });

  it('imports a VS Code colour theme with comments and trailing commas', () => {
    const t = importTheme(`{
      // Dracula-ish
      "name": "Night Owl",
      "type": "dark",
      "colors": {
        "editor.background": "#011627",
        "editor.foreground": "#d6deeb",
        "button.background": "#7e57c2dd", /* alpha is dropped */
        "sideBar.background": "#011627",
        "errorForeground": "#ef5350",
      },
      "tokenColors": [],
    }`);
    expect(t.name).toBe('Night Owl');
    expect(t.id).toBe('night-owl');
    expect(t.dark?.background).toBe('#011627');
    expect(t.dark?.accent).toBe('#7e57c2');
    expect(t.dark?.colors?.danger).toBe('#ef5350');
  });

  it('says why something is not a theme', () => {
    expect(() => importTheme('')).toThrow(ThemeFormatError);
    expect(() => importTheme('hello')).toThrow(/not a theme/);
    expect(() => importTheme('{"foo": 1}')).toThrow(/not a Cognitrix, Codex or VS Code theme/);
  });

  it('duplicates under a free id', () => {
    const copy = duplicateTheme(PRESETS[1], ['nord-copy']);
    expect(copy.id).toBe('nord-copy-2');
    expect(copy.name).toBe('Nord copy');
  });
});
