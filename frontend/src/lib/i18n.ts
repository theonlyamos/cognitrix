import { useCallback, useSyncExternalStore } from 'react';
import en, { type MessageKey, type Messages } from '@/locales/en';
import es from '@/locales/es';
import fr from '@/locales/fr';

// Translations. English is the source; a language is one file in
// src/locales with the same keys. The choice is per device ('' follows the
// browser). To add a language: copy en.ts, translate the values, and list it
// in LANGUAGES below.

export const LANGUAGES = [
  { code: 'en', name: 'English', messages: en },
  { code: 'fr', name: 'Français', messages: fr },
  { code: 'es', name: 'Español', messages: es },
] as const satisfies readonly { code: string; name: string; messages: Messages }[];

export type LanguageCode = (typeof LANGUAGES)[number]['code'];

const KEY = 'language';
const CHANGED = 'cognitrix:language-changed';

const known = (code: string | null | undefined): LanguageCode | null =>
  LANGUAGES.find((l) => l.code === code)?.code ?? null;

/** The person's own choice, or '' when they follow the browser. */
export function getLanguageChoice(): LanguageCode | '' {
  try {
    return known(localStorage.getItem(KEY)) ?? '';
  } catch {
    return '';
  }
}

function browserLanguage(): LanguageCode {
  const wanted = typeof navigator === 'undefined' ? [] : navigator.languages?.length ? navigator.languages : [navigator.language];
  for (const tag of wanted) {
    const match = known(String(tag || '').toLowerCase().split('-')[0]);
    if (match) return match;
  }
  return 'en';
}

export const getLanguage = (): LanguageCode => getLanguageChoice() || browserLanguage();

export function setLanguage(code: LanguageCode | '') {
  try {
    if (code) localStorage.setItem(KEY, code);
    else localStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
  applyLanguage();
  window.dispatchEvent(new Event(CHANGED));
}

/** Tell the browser (and screen readers) which language the page is in. */
export function applyLanguage() {
  if (typeof document !== 'undefined') document.documentElement.lang = getLanguage();
}

export function translate(language: LanguageCode, key: MessageKey, vars?: Record<string, string | number>): string {
  const messages: Messages = LANGUAGES.find((l) => l.code === language)?.messages ?? en;
  const text = messages[key] ?? en[key] ?? key;
  return vars ? text.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole)) : text;
}

function subscribe(notify: () => void) {
  window.addEventListener(CHANGED, notify);
  window.addEventListener('storage', notify);
  return () => {
    window.removeEventListener(CHANGED, notify);
    window.removeEventListener('storage', notify);
  };
}

export const useLanguage = () => useSyncExternalStore(subscribe, getLanguage, getLanguage);

/** `t('nav.inbox')` in the current language; re-renders when it changes. */
export function useT() {
  const language = useLanguage();
  return useCallback(
    (key: MessageKey, vars?: Record<string, string | number>) => translate(language, key, vars),
    [language],
  );
}

export type { MessageKey };
