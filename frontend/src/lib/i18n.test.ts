import { afterEach, describe, expect, it } from 'vitest';
import en from '@/locales/en';
import { LANGUAGES, getLanguage, setLanguage, translate } from './i18n';

afterEach(() => setLanguage(''));

describe('language files', () => {
  const keys = Object.keys(en).sort();
  const placeholders = (text: string) => (text.match(/\{\w+\}/g) ?? []).sort().join(',');

  for (const language of LANGUAGES) {
    it(`${language.name} has every key, nothing extra, and the same placeholders`, () => {
      expect(Object.keys(language.messages).sort()).toEqual(keys);
      for (const key of keys as (keyof typeof en)[]) {
        expect(language.messages[key].trim(), key).not.toBe('');
        expect(placeholders(language.messages[key]), key).toBe(placeholders(en[key]));
      }
    });
  }
});

describe('translate', () => {
  it('fills in placeholders and follows the chosen language', () => {
    expect(translate('en', 'settings.noMatch', { query: 'x' })).toBe('No settings match “x”.');
    expect(translate('fr', 'nav.settings')).toBe('Paramètres');
    expect(getLanguage()).toBe('en');
    setLanguage('es');
    expect(getLanguage()).toBe('es');
    expect(document.documentElement.lang).toBe('es');
  });
});
