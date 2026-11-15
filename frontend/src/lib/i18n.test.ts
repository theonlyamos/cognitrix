import { afterEach, describe, expect, it } from 'vitest';
import en from '@/locales/en';
import { LANGUAGES, dateLocale, getLanguage, loadLanguage, setLanguage, tr, translate, trn } from './i18n';

afterEach(() => setLanguage(''));

describe('language files', () => {
  const keys = Object.keys(en).sort();
  const placeholders = (text: string) => (text.match(/\{\w+\}/g) ?? []).sort().join(',');

  for (const language of LANGUAGES) {
    it(`${language.name} has every key, nothing extra, and the same placeholders`, async () => {
      const messages = await language.load();
      expect(Object.keys(messages).sort()).toEqual(keys);
      for (const key of keys as (keyof typeof en)[]) {
        expect(messages[key].trim(), key).not.toBe('');
        expect(placeholders(messages[key]), key).toBe(placeholders(en[key]));
      }
    });
  }
});

describe('translate', () => {
  it('fills in placeholders and follows the chosen language', async () => {
    await loadLanguage('fr');
    expect(translate('en', 'settings.noMatch', { query: 'x' })).toBe('No settings match “x”.');
    expect(translate('fr', 'nav.settings')).toBe('Paramètres');
    expect(getLanguage()).toBe('en');
    setLanguage('es');
    expect(getLanguage()).toBe('es');
    expect(document.documentElement.lang).toBe('es');
  });
});

describe('tr and trn', () => {
  it('follow the chosen language, with each language\'s plural rule', async () => {
    expect(tr('ui.count.tools_one', { n: 1 })).toBe('1 tool');
    expect(trn(0, 'ui.count.tools_one', 'ui.count.tools_other')).toBe('0 tools');
    expect(dateLocale()).toBeUndefined();
    await loadLanguage('fr');
    setLanguage('fr');
    expect(dateLocale()).toBe('fr');
    // French counts zero as singular
    expect(trn(0, 'ui.count.tools_one', 'ui.count.tools_other')).toBe(tr('ui.count.tools_one', { n: 0 }));
    expect(tr('ui.count.tools_other', { n: 2 })).not.toBe('2 tools');
  });
});
