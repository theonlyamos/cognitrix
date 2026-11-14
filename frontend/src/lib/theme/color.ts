// Small colour toolkit for themes: hex <-> sRGB <-> OKLab/OKLCH, WCAG
// contrast, and "nudge this colour until it reads on these surfaces".
// OKLCH keeps hue and saturation steady while lightness moves, so a nudged
// accent still looks like the accent the person picked.

export interface Rgb { r: number; g: number; b: number } // 0..1, sRGB
interface Lab { L: number; a: number; b: number }
export interface Lch { L: number; C: number; h: number }

const HEX = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i;

export function isHex(value: unknown): value is string {
  return typeof value === 'string' && HEX.test(value.trim());
}

/** "#abc" / "abc" / "#aabbcc" → "#aabbcc" (lower case), or null. */
export function normalizeHex(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const m = HEX.exec(value.trim());
  if (!m) return null;
  const body = m[1].length === 3 ? m[1].split('').map((c) => c + c).join('') : m[1];
  return `#${body.toLowerCase()}`;
}

export function hexToRgb(hex: string): Rgb {
  const h = normalizeHex(hex) ?? '#000000';
  return {
    r: parseInt(h.slice(1, 3), 16) / 255,
    g: parseInt(h.slice(3, 5), 16) / 255,
    b: parseInt(h.slice(5, 7), 16) / 255,
  };
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

export function rgbToHex({ r, g, b }: Rgb): string {
  return `#${[r, g, b].map((c) => Math.round(clamp01(c) * 255).toString(16).padStart(2, '0')).join('')}`;
}

/** "r g b" channels (0-255) for the `--x-rgb` twins Tailwind uses for opacity. */
export function rgbChannels(hex: string): string {
  const { r, g, b } = hexToRgb(hex);
  return [r, g, b].map((c) => Math.round(c * 255)).join(' ');
}

const toLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const fromLinear = (c: number) => (c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055);

function rgbToLab({ r, g, b }: Rgb): Lab {
  const [lr, lg, lb] = [toLinear(r), toLinear(g), toLinear(b)];
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return {
    L: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  };
}

/** Unclamped: channels outside 0..1 mean the colour is out of sRGB gamut. */
function labToRgbRaw({ L, a, b }: Lab): Rgb {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return {
    r: fromLinear(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    g: fromLinear(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    b: fromLinear(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  };
}

export function hexToLch(hex: string): Lch {
  const { L, a, b } = rgbToLab(hexToRgb(hex));
  const C = Math.sqrt(a * a + b * b);
  const h = ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360;
  return { L, C, h };
}

const inGamut = ({ r, g, b }: Rgb) => [r, g, b].every((c) => c >= -1e-4 && c <= 1 + 1e-4);

/** OKLCH → hex, lowering chroma (keeping lightness and hue) until it fits sRGB. */
export function lchToHex({ L, C, h }: Lch): string {
  const Lc = clamp01(L);
  const rad = (h * Math.PI) / 180;
  const at = (c: number) => labToRgbRaw({ L: Lc, a: c * Math.cos(rad), b: c * Math.sin(rad) });
  let rgb = at(C);
  if (!inGamut(rgb)) {
    let lo = 0;
    let hi = C;
    for (let i = 0; i < 20; i++) {
      const mid = (lo + hi) / 2;
      if (inGamut(at(mid))) lo = mid; else hi = mid;
    }
    rgb = at(lo);
  }
  return rgbToHex(rgb);
}

/** Mix two colours in OKLab: t=0 gives `a`, t=1 gives `b`. */
export function mix(a: string, b: string, t: number): string {
  const x = rgbToLab(hexToRgb(a));
  const y = rgbToLab(hexToRgb(b));
  return rgbToHex(labToRgbRaw({ L: x.L + (y.L - x.L) * t, a: x.a + (y.a - x.a) * t, b: x.b + (y.b - x.b) * t }));
}

/** Move OKLCH lightness by `dL` (clamped), keeping hue and chroma. */
export function shiftLightness(hex: string, dL: number): string {
  const c = hexToLch(hex);
  return lchToHex({ ...c, L: c.L + dL });
}

export function luminance(hex: string): number {
  const { r, g, b } = hexToRgb(hex);
  return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
}

export function contrastRatio(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** Lowest contrast of `color` against every surface. */
export function minContrast(color: string, surfaces: string[]): number {
  return Math.min(...surfaces.map((s) => contrastRatio(color, s)));
}

/**
 * `color`, made lighter (on dark surfaces) or darker (on light ones) only as
 * far as needed to reach `target` contrast against every surface. Keeps hue
 * and chroma, so the result still reads as the same colour. Returns the
 * closest it can get when the target is out of reach.
 */
export function readableOn(color: string, surfaces: string[], target: number): string {
  if (minContrast(color, surfaces) >= target) return color;
  const start = hexToLch(color);
  const darkSurfaces = surfaces.reduce((sum, s) => sum + luminance(s), 0) / surfaces.length < 0.18;
  const dir = darkSurfaces ? 1 : -1;
  let best = color;
  let bestScore = minContrast(color, surfaces);
  for (let step = 1; step <= 100; step++) {
    const L = start.L + dir * step * 0.01;
    if (L < 0 || L > 1) break;
    const candidate = lchToHex({ ...start, L });
    const score = minContrast(candidate, surfaces);
    if (score > bestScore) { best = candidate; bestScore = score; }
    if (score >= target) return candidate;
  }
  return best;
}

/** Black-ish or white text, whichever reads better on `fill`. */
export function textOn(fill: string, darkText = '#0b0c0e', lightText = '#ffffff'): string {
  return contrastRatio(fill, darkText) >= contrastRatio(fill, lightText) ? darkText : lightText;
}
