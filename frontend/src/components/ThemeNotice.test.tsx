import { describe, expect, it } from 'vitest';
import { themeChange } from './ThemeNotice';

describe('themeChange', () => {
  it('reads a Set Theme result', () => {
    expect(themeChange('Set Theme', 'Theme changed: Tokyo Night (light: Tokyo Night, dark: Tokyo Night, mode: dark). Previous: {"mode": "", "light": "signal", "dark": "signal"}'))
      .toEqual({ name: 'Tokyo Night', previous: { mode: '', light: 'signal', dark: 'signal' } });
  });

  it('reads a result that carries a note for the agent on later lines', () => {
    expect(themeChange('set_theme', 'Theme changed: Dusk (light: Signal, dark: Dusk, mode: dark). Previous: {"mode": "", "light": "signal", "dark": "signal", "created": "dusk"}\nThe theme has the built-in Dunes artwork for now. Next, draw artwork.'))
      .toEqual({ name: 'Dusk', previous: { mode: '', light: 'signal', dark: 'signal', created: 'dusk' } });
  });

  it('ignores other tools and failures', () => {
    expect(themeChange('Memory', 'Theme changed: x (y). Previous: {}')).toBeNull();
    expect(themeChange('set_theme', 'There is no theme called "Barbie".')).toBeNull();
    expect(themeChange('set_theme', null)).toBeNull();
  });
});
