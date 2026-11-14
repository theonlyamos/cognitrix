// Theme artwork: small hand-drawn SVG pictures shown in a band at the top of
// the sidebar and as a larger scene on new chats and the Dashboard.
//
// A drawing names its colours by theme token (fill="accent", stroke="fg"...)
// instead of hex, so it follows light and dark and any colour edits. It is
// always shown as an <img> (never inlined into the page), and what people
// paste or agents draw is cleaned down to plain shapes first. The backend
// cleans it the same way (cognitrix/themes.py `clean_artwork_svg`).

import type { TokenValues } from './themes';

export interface Artwork {
  /** A built-in drawing (ARTWORK_IDS), or 'none' to show nothing. */
  id?: string;
  /** A drawing of the person's or an agent's own (cleaned SVG). Wins over `id`. */
  svg?: string;
  name?: string;
  /** 0-100, default 85: how strongly it shows. */
  strength?: number;
}

export const DEFAULT_STRENGTH = 85;
export const MAX_ARTWORK_BYTES = 24 * 1024;
const MAX_ELEMENTS = 1500;
const SVG_NS = 'http://www.w3.org/2000/svg';

export class ArtworkError extends Error {}

// ---------------------------------------------------------------- colours

/** Words a drawing may use for colours. `surface` is whatever the drawing sits on. */
const ALIASES: Record<string, string> = { text: 'fg', background: 'bg', foreground: 'fg' };
const COLOR_WORDS: ReadonlySet<string> = new Set([
  'bg', 'panel', 'panel-2', 'fg', 'fg-dim', 'accent', 'accent-ink', 'on-accent',
  'danger', 'ok', 'warn', 'info', 'violet', 'surface',
]);

// ---------------------------------------------------------------- cleaning

const ELEMENTS: ReadonlySet<string> = new Set([
  'svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon',
  'defs', 'linearGradient', 'radialGradient', 'stop', 'mask', 'clipPath', 'filter', 'feGaussianBlur',
]);
const NUMBER = /^-?(\d+\.?\d*|\.\d+)(e-?\d+)?%?$/i;
const NUMBERS = /^[-\d.eE,\s]+$/;
const PATH = /^[MmLlHhVvCcSsQqTtAaZz\d.eE,\s+-]+$/;
const TRANSFORM = /^(\s*(matrix|translate|scale|rotate|skewX|skewY)\s*\([-\d.eE,\s]*\)\s*,?)+$/;
const ID = /^[A-Za-z][\w-]{0,40}$/;
const REF = /^url\(\s*#[A-Za-z][\w-]{0,40}\s*\)$/;
const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
const ENUMS: Record<string, ReadonlySet<string>> = {
  'stroke-linecap': new Set(['butt', 'round', 'square']),
  'stroke-linejoin': new Set(['miter', 'round', 'bevel']),
  'fill-rule': new Set(['nonzero', 'evenodd']),
  'clip-rule': new Set(['nonzero', 'evenodd']),
  gradientUnits: new Set(['userSpaceOnUse', 'objectBoundingBox']),
  maskUnits: new Set(['userSpaceOnUse', 'objectBoundingBox']),
  maskContentUnits: new Set(['userSpaceOnUse', 'objectBoundingBox']),
  clipPathUnits: new Set(['userSpaceOnUse', 'objectBoundingBox']),
  spreadMethod: new Set(['pad', 'reflect', 'repeat']),
};
const NUMERIC_ATTRS: ReadonlySet<string> = new Set([
  'cx', 'cy', 'r', 'rx', 'ry', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'width', 'height', 'fx', 'fy', 'offset',
  'opacity', 'fill-opacity', 'stroke-opacity', 'stop-opacity', 'stroke-width', 'stdDeviation', 'stroke-miterlimit',
]);
const COLOR_ATTRS: ReadonlySet<string> = new Set(['fill', 'stroke', 'stop-color']);

/** A cleaned attribute value, or null to drop it. */
export function cleanAttribute(name: string, raw: string): string | null {
  const v = raw.trim();
  if (!v || v.length > 4000) return null;
  if (COLOR_ATTRS.has(name)) {
    if (v === 'none' || REF.test(v)) return v.replace(/\s+/g, '');
    if (HEX.test(v)) return v.toLowerCase();
    const word = ALIASES[v.toLowerCase()] ?? v.toLowerCase();
    return COLOR_WORDS.has(word) ? word : null;
  }
  if (NUMERIC_ATTRS.has(name)) return NUMBER.test(v) ? v : null;
  if (name === 'points' || name === 'viewBox') return NUMBERS.test(v) ? v.replace(/\s+/g, ' ') : null;
  if (name === 'stroke-dasharray') return v === 'none' || NUMBERS.test(v) ? v.replace(/\s+/g, ' ') : null;
  if (name === 'd') return PATH.test(v) ? v.replace(/\s+/g, ' ') : null;
  if (name === 'transform' || name === 'gradientTransform') return TRANSFORM.test(v) ? v.replace(/\s+/g, ' ') : null;
  if (name === 'id') return ID.test(v) ? v : null;
  if (name === 'mask' || name === 'clip-path' || name === 'filter') return REF.test(v) ? v.replace(/\s+/g, '') : null;
  if (ENUMS[name]) return ENUMS[name].has(v) ? v : null;
  return null;
}

const escapeAttr = (v: string) => v.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

/** `style="fill:red; stroke-width:2"` → [['fill','red'], ['stroke-width','2']]. */
function styleDeclarations(style: string | null): [string, string][] {
  if (!style) return [];
  return style.split(';').map((d) => d.split(':')).filter((p) => p.length === 2)
    .map(([k, v]) => [k.trim(), v.trim()] as [string, string]);
}

function cleanElement(el: Element, budget: { left: number }): string {
  const name = el.localName;
  if ((el.namespaceURI && el.namespaceURI !== SVG_NS) || !ELEMENTS.has(name) || name === 'svg') return '';
  if (--budget.left < 0) throw new ArtworkError('This drawing has too many shapes.');
  const attrs: [string, string][] = [];
  const seen = new Set<string>();
  const add = (attr: string, value: string) => {
    if (seen.has(attr)) return;
    const clean = cleanAttribute(attr, value);
    if (clean !== null) { attrs.push([attr, clean]); seen.add(attr); }
  };
  for (const a of Array.from(el.attributes)) if (!a.namespaceURI && a.localName !== 'style') add(a.localName, a.value);
  for (const [k, v] of styleDeclarations(el.getAttribute('style'))) add(k, v);
  const inner = Array.from(el.children).map((c) => cleanElement(c, budget)).join('');
  const open = `<${name}${attrs.map(([k, v]) => ` ${k}="${escapeAttr(v)}"`).join('')}`;
  return inner ? `${open}>${inner}</${name}>` : `${open}/>`;
}

/**
 * Clean SVG down to plain shapes, gradients, masks and blur: no scripts,
 * links, images, text, styles or outside references. Colours must be hex or
 * a theme word (accent, fg, text, surface...). Throws ArtworkError.
 */
export function cleanArtworkSvg(source: string): string {
  const text = source.trim();
  if (!text) throw new ArtworkError('Paste an SVG drawing first.');
  if (text.length > MAX_ARTWORK_BYTES) throw new ArtworkError('This drawing is too large (keep it under 24 KB).');
  if (/<!(DOCTYPE|ENTITY)/i.test(text)) throw new ArtworkError('This drawing uses a DOCTYPE, which is not allowed.');
  const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
  const root = doc.documentElement;
  if (!root || root.localName !== 'svg' || doc.getElementsByTagName('parsererror').length) {
    throw new ArtworkError('This is not an SVG drawing.');
  }
  let viewBox = cleanAttribute('viewBox', root.getAttribute('viewBox') ?? '');
  if (!viewBox) {
    const w = Number.parseFloat(root.getAttribute('width') ?? '');
    const h = Number.parseFloat(root.getAttribute('height') ?? '');
    if (!(w > 0 && h > 0)) throw new ArtworkError('The drawing needs a viewBox, such as viewBox="0 0 400 160".');
    viewBox = `0 0 ${w} ${h}`;
  }
  if (viewBox.split(/[\s,]+/).filter(Boolean).length !== 4) throw new ArtworkError('The viewBox needs four numbers.');
  const budget = { left: MAX_ELEMENTS };
  const inner = Array.from(root.children).map((c) => cleanElement(c, budget)).join('');
  if (!inner) throw new ArtworkError('Nothing in this drawing could be kept.');
  return `<svg xmlns="${SVG_NS}" viewBox="${viewBox}">${inner}</svg>`;
}

// ---------------------------------------------------------------- showing

/** Theme words → the colours in use. `surface` is the panel the drawing sits on. */
export function artworkColors(tokens: TokenValues, surface: string): Record<string, string> {
  const out: Record<string, string> = { surface };
  for (const word of COLOR_WORDS) if (word !== 'surface') out[word] = tokens[word as keyof TokenValues];
  return out;
}

/** A data: URL for an <img>, with theme words swapped for colours. */
export function artworkImage(svg: string, colors: Record<string, string>): string {
  const painted = svg.replace(/(fill|stroke|stop-color)="([a-z][a-z0-9-]*)"/g,
    (all, attr: string, word: string) => (colors[word] ? `${attr}="${colors[word]}"` : all));
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(painted)}`;
}

// ---------------------------------------------------------------- built-in drawings
// All 400 x 160 with the ground along the bottom, so they crop well as a band
// (keeps the bottom) or as a wider scene.

const rng = (seed: number) => { let s = seed; return () => ((s = (s * 16807) % 2147483647) - 1) / 2147483646; };
const f = (n: number) => n.toFixed(1);

function stars(n: number, seed: number, maxY: number): string {
  const r = rng(seed);
  let o = '';
  for (let i = 0; i < n; i++) {
    const x = r() * 400, y = 4 + r() * maxY, big = r() < 0.14;
    o += `<circle cx="${f(x)}" cy="${f(y)}" r="${big ? 1.3 : 0.7}" fill="fg" opacity="${(0.3 + r() * 0.5).toFixed(2)}"/>`;
  }
  return o;
}
const sparkle = (x: number, y: number, s: number, fill: string, op = 1) =>
  `<path d="M${x} ${y - s}Q${x} ${y} ${x + s} ${y}Q${x} ${y} ${x} ${y + s}Q${x} ${y} ${x - s} ${y}Q${x} ${y} ${x} ${y - s}Z" fill="${fill}" opacity="${op}"/>`;
/** A shape that hides what is behind it, tinted with text colour. */
const solid = (tag: string, attrs: string, tint: number) =>
  `<${tag} ${attrs} fill="surface"/><${tag} ${attrs} fill="fg" opacity="${tint}"/>`;

const DRAW: Record<string, () => string> = {
  signal() {
    let o = '';
    for (let x = 8; x < 400; x += 16) for (let y = 8; y < 160; y += 16) o += `<circle cx="${x}" cy="${y}" r="0.8" fill="fg" opacity="0.13"/>`;
    const cx = 322, cy = 96;
    for (let i = 1; i <= 5; i++) {
      const r = i * 17;
      o += `<path d="M${cx - r} ${cy}A${r} ${r} 0 0 1 ${cx + r} ${cy}" fill="none" stroke="accent" stroke-width="1.6" stroke-linecap="round" opacity="${(1 - i * 0.16).toFixed(2)}"${i % 2 ? '' : ' stroke-dasharray="2 5"'}/>`;
    }
    o += `<path d="M${cx} ${cy}L${cx - 15} 160M${cx} ${cy}L${cx + 15} 160M${cx - 5} 116H${cx + 5}M${cx - 9} 134H${cx + 9}M${cx - 13} 152H${cx + 13}M${cx - 5} 116L${cx + 9} 134M${cx + 5} 116L${cx - 9} 134M${cx - 9} 134L${cx + 13} 152M${cx + 9} 134L${cx - 13} 152" stroke="fg" stroke-width="1.4" opacity="0.6" fill="none" stroke-linecap="round"/>`;
    o += `<circle cx="${cx}" cy="${cy}" r="3.5" fill="accent"/>`;
    const p: string[] = [], q: string[] = [];
    for (let x = 0; x <= 288; x += 3) {
      const amp = 2 + (x / 288) ** 2 * 16;
      p.push(`${x},${f(128 + Math.sin(x / 7) * amp * Math.sin(x / 41 + 1))}`);
      q.push(`${x},${f(140 + Math.sin(x / 11 + 2) * amp * 0.6)}`);
    }
    o += `<polyline points="${q.join(' ')}" fill="none" stroke="info" stroke-width="1.2" opacity="0.55"/>`;
    o += `<polyline points="${p.join(' ')}" fill="none" stroke="accent" stroke-width="1.6" stroke-linejoin="round"/>`;
    return o;
  },
  aurora() {
    let o = '<defs><linearGradient id="au" x1="0" x2="1"><stop offset="0" stop-color="accent" stop-opacity="0"/><stop offset="0.3" stop-color="accent" stop-opacity="0.9"/><stop offset="0.7" stop-color="violet" stop-opacity="0.8"/><stop offset="1" stop-color="violet" stop-opacity="0"/></linearGradient><filter id="bl" x="-10%" y="-50%" width="120%" height="200%"><feGaussianBlur stdDeviation="3.2"/></filter></defs>';
    o += stars(46, 7, 100);
    o += '<g filter="url(#bl)" fill="none" stroke="url(#au)" stroke-linecap="round"><path d="M-10 66C70 24 140 92 220 46S350 22 410 52" stroke-width="14" opacity="0.55"/><path d="M-10 84C80 50 150 104 240 66S360 54 410 74" stroke-width="9" opacity="0.4"/><path d="M20 46C90 14 170 60 250 30S370 18 400 30" stroke-width="5" opacity="0.5"/></g>';
    o += '<g stroke="accent" opacity="0.18" stroke-width="1">';
    for (let i = 0; i < 18; i++) { const x = 40 + i * 18; o += `<path d="M${x} ${f(44 + Math.sin(i) * 10)}V${f(82 + Math.cos(i) * 8)}"/>`; }
    o += '</g>';
    o += solid('polygon', 'points="0,160 0,118 40,98 70,110 112,76 150,104 188,88 228,112 272,80 318,108 352,92 400,112 400,160"', 0.1);
    o += '<g fill="none" stroke="fg" stroke-width="1.3" opacity="0.45" stroke-linejoin="round"><path d="M100 85L112 76L124 86"/><path d="M262 87L272 80L283 89"/><path d="M180 92L188 88L196 93"/></g>';
    o += solid('polygon', 'points="0,160 0,136 52,120 92,132 140,108 190,136 240,122 300,140 350,118 400,134 400,160"', 0.18);
    return o;
  },
  dunes() {
    let o = '<circle cx="282" cy="108" r="30" fill="warn"/>';
    for (let i = 0; i < 11; i++) {
      const a = Math.PI + (i + 0.5) * Math.PI / 11, r1 = 39, r2 = i % 2 ? 50 : 56;
      o += `<line x1="${f(282 + Math.cos(a) * r1)}" y1="${f(108 + Math.sin(a) * r1)}" x2="${f(282 + Math.cos(a) * r2)}" y2="${f(108 + Math.sin(a) * r2)}" stroke="warn" stroke-width="2.2" stroke-linecap="round" opacity="0.85"/>`;
    }
    o += '<rect x="248" y="104" width="68" height="3" fill="surface"/><rect x="248" y="112" width="68" height="2.4" fill="surface"/>';
    o += solid('path', 'd="M0 160L0 120Q70 94 150 116T300 110T400 118L400 160Z"', 0.09);
    o += '<path d="M0 120Q70 94 150 116T300 110T400 118" fill="none" stroke="fg" stroke-width="1.2" opacity="0.35"/>';
    o += solid('path', 'd="M0 160L0 140Q90 116 200 138T400 130L400 160Z"', 0.16);
    o += '<path d="M0 140Q90 116 200 138T400 130" fill="none" stroke="accent" stroke-width="1.5" opacity="0.7"/>';
    o += '<g fill="none" stroke="fg-dim" stroke-width="1" opacity="0.45">';
    for (let i = 0; i < 4; i++) o += `<path d="M${30 + i * 26} ${148 + (i % 2) * 4}q8 -4 16 0"/>`;
    o += '</g><g fill="none" stroke="fg" stroke-width="1.3" stroke-linecap="round" opacity="0.6"><path d="M120 50q5 -5 10 0q5 -5 10 0"/><path d="M150 38q4 -4 8 0q4 -4 8 0"/></g>';
    return o;
  },
  'moon-cat'() {
    let o = stars(30, 11, 120);
    o += '<defs><mask id="mm"><rect width="400" height="160" fill="#ffffff"/><circle cx="324" cy="44" r="30" fill="#000000"/></mask></defs>';
    o += '<circle cx="306" cy="56" r="34" fill="accent" mask="url(#mm)"/>';
    o += sparkle(250, 30, 6, 'violet') + sparkle(356, 104, 4.5, 'accent', 0.9) + sparkle(60, 40, 5, 'violet', 0.8) + sparkle(200, 64, 3.5, 'accent', 0.7);
    o += '<g fill="fg" opacity="0.1"><circle cx="70" cy="150" r="26"/><circle cx="110" cy="140" r="30"/><circle cx="150" cy="150" r="24"/><circle cx="185" cy="156" r="18"/><rect x="40" y="148" width="170" height="20"/></g>';
    o += '<g fill="violet" fill-opacity="0.28" stroke="fg" stroke-opacity="0.75" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"><path d="M86 130C86 110 108 100 128 102C150 104 160 118 156 130C152 140 136 144 118 143C100 142 86 140 86 130Z"/><path d="M150 131C162 134 160 146 146 146C130 146 106 146 96 140" fill="none"/><path d="M84 132C78 126 80 114 88 110L90 101L96 108C102 106 108 107 112 110L117 103L118 113C122 118 120 128 114 132C106 137 90 137 84 132Z"/><path d="M91 123q3 2 6 0M103 123q3 2 6 0" fill="none"/></g>';
    o += '<g fill="none" stroke="accent" stroke-linejoin="round" stroke-linecap="round"><path d="M120 92h7l-7 7h7" stroke-width="1.6"/><path d="M132 80h5l-5 5h5" stroke-width="1.3" opacity="0.8"/><path d="M141 70h4l-4 4h4" stroke-width="1.1" opacity="0.6"/></g>';
    o += '<path d="M156 128C180 120 196 140 214 138" fill="none" stroke="warn" stroke-width="1.2" opacity="0.8"/><circle cx="224" cy="140" r="11" fill="warn" opacity="0.9"/><g fill="none" stroke="surface" stroke-width="1.1" opacity="0.7"><path d="M215 135q9 4 17 -2M214 142q10 4 20 -1M219 131q-2 10 4 20"/></g>';
    return o;
  },
  skyline() {
    let o = stars(40, 3, 70);
    o += '<circle cx="330" cy="36" r="26" fill="fg" opacity="0.06"/><circle cx="330" cy="36" r="13" fill="fg" opacity="0.85"/><circle cx="335" cy="33" r="11" fill="surface" opacity="0.9"/>';
    const r = rng(19);
    let back = '', front = '', win = '';
    for (let x = -6; x < 400;) { const w = 16 + r() * 22, h = 34 + r() * 52; back += `<rect x="${f(x)}" y="${f(160 - h)}" width="${f(w)}" height="${f(h)}"/>`; x += w + 2; }
    for (let x = -4; x < 400;) {
      const w = 22 + r() * 26, h = 22 + r() * 44, y = 160 - h;
      front += `<rect x="${f(x)}" y="${f(y)}" width="${f(w)}" height="${f(h)}"/>`;
      for (let wy = y + 6; wy < 156; wy += 7) for (let wx = x + 4; wx < x + w - 4; wx += 6) {
        const v = r();
        if (v < 0.3) win += `<rect x="${f(wx)}" y="${f(wy)}" width="2.6" height="3.2" fill="${v < 0.1 ? 'warn' : 'accent'}" opacity="${(0.5 + r() * 0.5).toFixed(2)}"/>`;
      }
      x += w + 3;
    }
    o += `<g fill="fg" opacity="0.08">${back}</g>`;
    o += '<g stroke="accent" stroke-width="1.1" fill="none" opacity="0.75"><path d="M128 160L140 56L152 160M131 132H149M134 104H146M137 80H143M131 132L146 104M149 132L134 104M134 104L143 80M146 104L137 80M140 56V44"/></g><circle cx="140" cy="43" r="2" fill="warn"/>';
    o += `<g fill="surface">${front}</g><g fill="fg" opacity="0.15">${front}</g>${win}`;
    return o;
  },
  halftone() {
    let o = '';
    for (let x = 6; x < 400; x += 12) for (let y = 6; y < 160; y += 12) {
      const t = Math.max(0, (x / 400) * 0.8 + (y / 160) * 0.5 - 0.45);
      if (t > 0.02) o += `<circle cx="${x}" cy="${y}" r="${(t * 6).toFixed(2)}" fill="fg"/>`;
    }
    return `${o}<rect x="0" y="150" width="400" height="10" fill="accent"/>`;
  },
  blueprint() {
    let o = '';
    for (let x = 0; x <= 400; x += 16) o += `<line x1="${x}" y1="0" x2="${x}" y2="160" stroke="accent" stroke-width="${x % 64 ? 0.5 : 1}" opacity="${x % 64 ? 0.18 : 0.35}"/>`;
    for (let y = 0; y <= 160; y += 16) o += `<line x1="0" y1="${y}" x2="400" y2="${y}" stroke="accent" stroke-width="${y % 64 ? 0.5 : 1}" opacity="${y % 64 ? 0.18 : 0.35}"/>`;
    o += '<g fill="none" stroke="fg" stroke-width="1.3" opacity="0.7"><circle cx="300" cy="84" r="40"/><circle cx="300" cy="84" r="14"/>';
    for (let i = 0; i < 10; i++) {
      const a = i * Math.PI / 5;
      const pt = (da: number, rr: number) => `${f(300 + Math.cos(a + da) * rr)} ${f(84 + Math.sin(a + da) * rr)}`;
      o += `<path d="M${pt(-0.14, 40)}L${pt(-0.1, 48)}L${pt(0.1, 48)}L${pt(0.14, 40)}"/>`;
    }
    o += '</g><g stroke="accent" stroke-width="1" fill="none"><path d="M236 84H210M300 140V150M252 150H348M252 146V154M348 146V154"/><path d="M90 40H190V120H90Z" stroke-dasharray="4 3"/><path d="M90 30H190M90 26V34M190 26V34M90 40L190 120"/></g><circle cx="300" cy="84" r="2.5" fill="accent"/>';
    return o;
  },
};

export const ARTWORK_NAMES: Record<string, string> = {
  signal: 'Signal', aurora: 'Aurora', dunes: 'Dunes', 'moon-cat': 'Moon cat',
  skyline: 'Skyline', halftone: 'Halftone', blueprint: 'Blueprint',
};
/** Built-in drawings, in picker order. cognitrix/themes.py ARTWORK_IDS must match. */
export const ARTWORK_IDS = Object.keys(ARTWORK_NAMES);

const builtCache = new Map<string, string>();
export function builtInArtwork(id: string): string | null {
  if (!DRAW[id]) return null;
  let svg = builtCache.get(id);
  if (!svg) {
    svg = `<svg xmlns="${SVG_NS}" viewBox="0 0 400 160">${DRAW[id]()}</svg>`;
    builtCache.set(id, svg);
  }
  return svg;
}

/** The SVG to show for an artwork setting, or null for none. */
export function artworkSvg(art: Artwork | null | undefined): string | null {
  if (!art) return null;
  if (art.svg) return art.svg;
  return art.id && art.id !== 'none' ? builtInArtwork(art.id) : null;
}

export function artworkName(art: Artwork | null | undefined): string {
  if (!art) return 'None';
  if (art.svg) return art.name || 'Your drawing';
  return art.id ? ARTWORK_NAMES[art.id] ?? 'None' : 'None';
}

/** Check and tidy an artwork setting (theme files, the account, pasted SVG). */
export function parseArtwork(raw: unknown): Artwork | undefined {
  if (raw === undefined || raw === null) return undefined;
  if (typeof raw !== 'object' || Array.isArray(raw)) throw new ArtworkError('"artwork" must be an object.');
  const a = raw as Record<string, unknown>;
  const out: Artwork = {};
  if (typeof a.svg === 'string' && a.svg.trim()) {
    out.svg = cleanArtworkSvg(a.svg);
    out.name = typeof a.name === 'string' && a.name.trim() ? a.name.trim().slice(0, 48) : 'Your drawing';
  } else if (a.id === 'none' || (typeof a.id === 'string' && DRAW[a.id])) {
    out.id = a.id;
  } else {
    throw new ArtworkError(`Unknown artwork "${String(a.id ?? '')}". Choose from: ${ARTWORK_IDS.join(', ')}, or none.`);
  }
  if (a.strength !== undefined && a.strength !== null) {
    const s = Number(a.strength);
    if (Number.isFinite(s)) out.strength = Math.round(Math.min(100, Math.max(0, s)));
  }
  return out;
}
