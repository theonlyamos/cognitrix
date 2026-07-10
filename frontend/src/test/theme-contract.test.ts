import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), 'src/app.css'), 'utf8');
const config = readFileSync(resolve(process.cwd(), 'tailwind.config.js'), 'utf8');

const lightTheme = /:root\s*\{([\s\S]*?)\}/.exec(css)?.[1] ?? '';
const darkTheme = /html\.dark\s*\{([\s\S]*?)\}/.exec(css)?.[1] ?? '';
const backgroundTokens = ['bg', 'panel', 'panel-2'] as const;

function token(block: string, name: string) {
  return new RegExp(`--${name}:([^;]+);`).exec(block)?.[1].trim() ?? '';
}

function rgb(hex: string) {
  const value = hex.replace('#', '');
  expect(value, `${hex} must be a six-digit hex color`).toMatch(/^[0-9a-f]{6}$/i);
  return [0, 2, 4].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16));
}

function relativeLuminance(hex: string) {
  const channels = rgb(hex).map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4;
  });

  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrastRatio(first: string, second: string) {
  const lighter = Math.max(relativeLuminance(first), relativeLuminance(second));
  const darker = Math.min(relativeLuminance(first), relativeLuminance(second));
  return (lighter + 0.05) / (darker + 0.05);
}

describe('Technical / Signal theme contract', () => {
  it('exports mode-specific focus colors', () => {
    expect(css).toContain('--focus:#4e6b00');
    expect(css).toContain('--focus:#c6f24e');
  });

  it('backs semantic Tailwind colors with alpha-aware RGB channels', () => {
    expect(config).toContain('rgb(var(--accent-rgb) / <alpha-value>)');
    expect(config).toContain('rgb(var(--danger-rgb) / <alpha-value>)');
    expect(config).toContain('rgb(var(--ok-rgb) / <alpha-value>)');
  });

  it('keeps each focus color at 3:1 contrast against its theme surfaces', () => {
    const themes = [
      { name: 'light', block: lightTheme, focus: '#4e6b00' },
      { name: 'dark', block: darkTheme, focus: '#c6f24e' },
    ];

    for (const theme of themes) {
      expect(token(theme.block, 'focus'), `${theme.name} focus token`).toBe(theme.focus);

      for (const background of backgroundTokens) {
        const ratio = contrastRatio(theme.focus, token(theme.block, background));
        expect(ratio, `${theme.name} focus against --${background}`).toBeGreaterThanOrEqual(3);
      }
    }
  });
});
