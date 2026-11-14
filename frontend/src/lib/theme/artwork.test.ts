import { describe, expect, it } from 'vitest';
import {
  ARTWORK_IDS, ArtworkError, artworkColors, artworkImage, builtInArtwork, cleanArtworkSvg, parseArtwork,
} from './artwork';
import { PRESETS, parseThemeFile, themeTokens } from './themes';
import { artworkFor } from './store';

// The same input as tests/test_themes.py, so both sides clean alike.
const DIRTY = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 400 160" onload="x()">'
  + '<script>alert(1)</script><style>*{}</style><foreignObject><div/></foreignObject>'
  + '<image href="https://evil.example/a.png"/><use xlink:href="#a"/><text>hi</text>'
  + '<a href="javascript:x"><rect width="9" height="9"/></a>'
  + '<circle cx="10" cy="10" r="5" fill="text" onclick="x()" style="stroke:accent; opacity:.5"/>'
  + '<rect x="1" y="1" width="2" height="2" fill="url(https://evil)" stroke="red" transform="rotate(4) url(x)"/>'
  + '<path d="M0 0 L10 10 Z" fill="#ABC" mask="url(#m)" filter="url(http://x)"/>'
  + '</svg>';
const CLEAN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160">'
  + '<circle cx="10" cy="10" r="5" fill="fg" stroke="accent" opacity=".5"/>'
  + '<rect x="1" y="1" width="2" height="2"/>'
  + '<path d="M0 0 L10 10 Z" fill="#abc" mask="url(#m)"/></svg>';

describe('artwork', () => {
  it('cleans SVG down to plain shapes, the same way the server does', () => {
    expect(cleanArtworkSvg(DIRTY)).toBe(CLEAN);
    expect(cleanArtworkSvg(CLEAN)).toBe(CLEAN);
  });

  it.each([
    ['', /Paste/],
    ['<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg viewBox="0 0 4 4">&a;</svg>', /DOCTYPE/],
    ['<html><body/></html>', /not an SVG/],
    ['<svg><circle r="1"/></svg>', /viewBox/],
    ['<svg viewBox="0 0 4 4"><script>x</script></svg>', /Nothing/],
  ])('refuses %j', (svg, reason) => {
    expect(() => cleanArtworkSvg(svg)).toThrow(ArtworkError);
    expect(() => cleanArtworkSvg(svg)).toThrow(reason);
  });

  it.each(ARTWORK_IDS)('built-in %s passes its own cleaning unchanged', (id) => {
    const svg = builtInArtwork(id)!;
    expect(svg.length).toBeLessThan(24 * 1024);
    expect(cleanArtworkSvg(svg)).toBe(svg);
  });

  it('paints theme words with the theme colours', () => {
    const nord = PRESETS.find((t) => t.id === 'nord')!;
    const tokens = themeTokens(nord, 'dark');
    const url = artworkImage('<svg viewBox="0 0 4 4"><rect fill="accent" stroke="surface"/><stop stop-color="fg"/></svg>', artworkColors(tokens, tokens.panel));
    const svg = decodeURIComponent(url.split(',')[1]);
    expect(url.startsWith('data:image/svg+xml')).toBe(true);
    expect(svg).toContain(`fill="${tokens.accent}"`);
    expect(svg).toContain(`stroke="${tokens.panel}"`);
    expect(svg).toContain(`stop-color="${tokens.fg}"`);
  });

  it('theme files and the person’s choice decide what shows', () => {
    const nord = PRESETS.find((t) => t.id === 'nord')!;
    expect(nord.artwork).toEqual({ id: 'aurora' });
    const selection = { light: 'nord', dark: 'nord', themes: [], artwork: {} };
    expect(artworkFor(selection, nord)).toEqual({ id: 'aurora' });
    expect(artworkFor({ ...selection, artwork: { nord: { id: 'none' } } }, nord)).toBeNull();
    expect(artworkFor({ ...selection, artwork: { nord: { id: 'skyline' } } }, nord)).toEqual({ id: 'skyline' });
    expect(() => parseThemeFile({ ...nord, artwork: { id: 'mona-lisa' } })).toThrow(/Unknown artwork/);
    expect(parseArtwork({ svg: '<svg viewBox="0 0 4 4"><circle r="1" fill="text"/></svg>', strength: 400 }))
      .toEqual({ svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><circle r="1" fill="fg"/></svg>', name: 'Your drawing', strength: 100 });
  });
});

describe('artwork transforms', () => {
  it('accepts lists of transforms and refuses anything else, quickly', async () => {
    const { cleanAttribute } = await import('./artwork');
    expect(cleanAttribute('transform', 'translate(10 20) rotate(45), scale(2)')).toBe('translate(10 20) rotate(45), scale(2)');
    expect(cleanAttribute('transform', 'rotate(4) url(x)')).toBeNull();
    const start = Date.now();
    expect(cleanAttribute('transform', `scale()${' scale()'.repeat(400)}!`)).toBeNull();
    expect(Date.now() - start).toBeLessThan(200);
  });
});
