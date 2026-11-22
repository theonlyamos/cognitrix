import { beforeEach, describe, expect, it } from 'vitest';
import { getUiMode, isLandingPath, isModeSwitchKey, setUiMode } from '@/lib/uiMode';

describe('uiMode', () => {
  beforeEach(() => localStorage.clear());

  it('remembers chill mode, full by default', () => {
    expect(getUiMode()).toBe('full');
    setUiMode('chill');
    expect(getUiMode()).toBe('chill');
  });

  it('switches with Ctrl+Shift+.', () => {
    const key = (init: KeyboardEventInit) => new KeyboardEvent('keydown', init);
    expect(isModeSwitchKey(key({ ctrlKey: true, shiftKey: true, code: 'Period', key: '>' }))).toBe(true);
    expect(isModeSwitchKey(key({ metaKey: true, shiftKey: true, code: 'Period', key: '.' }))).toBe(true);
    // Ctrl+. alone is the chat's side panel.
    expect(isModeSwitchKey(key({ ctrlKey: true, code: 'Period', key: '.' }))).toBe(false);
  });

  it('only replaces the pages the app opens on', () => {
    expect(isLandingPath('/home', '')).toBe(true);
    expect(isLandingPath('/dashboard', '')).toBe(true);
    expect(isLandingPath('/home', '?chat=1')).toBe(false);
    expect(isLandingPath('/agents', '')).toBe(false);
  });
});
