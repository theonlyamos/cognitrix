import { Fragment, createElement, useCallback, useSyncExternalStore, type ReactNode } from 'react';
import en, { type MessageKey, type Messages } from '@/locales/en';

// Translations. English is the source; a language is one file in
// src/locales with the same keys (the screens' text is in ui.<code>.ts, made
// by scripts/i18n-extract.mjs and scripts/i18n-translate.mjs). The choice is
// per device ('' follows the browser). Other languages load on demand so
// English-only visitors don't download them. To add a language: copy en.ts,
// translate the values, and list it in LANGUAGES below.

export const LANGUAGES = [
  { code: 'en', name: 'English', load: async () => en },
  { code: 'fr', name: 'Français', load: async () => (await import('@/locales/fr')).default },
  { code: 'es', name: 'Español', load: async () => (await import('@/locales/es')).default },
] as const satisfies readonly { code: string; name: string; load: () => Promise<Messages> }[];

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

// tr() runs for every piece of text on every render, so the language is
// worked out once and kept until it changes.
let cached: LanguageCode | null = null;

export const getLanguage = (): LanguageCode => (cached ??= getLanguageChoice() || browserLanguage());

/**
 * Locale for dates and numbers: the language picked in Appearance, or
 * undefined to keep the browser's own (which also knows the region).
 */
export const dateLocale = (): string | undefined => getLanguageChoice() || undefined;

const loaded: Partial<Record<LanguageCode, Messages>> = { en };

/** Fetch a language's text; resolves once translate() can use it. */
export async function loadLanguage(code: LanguageCode = getLanguage()): Promise<void> {
  if (loaded[code]) return;
  const language = LANGUAGES.find((l) => l.code === code);
  try {
    if (language) loaded[code] = await language.load();
  } catch {
    /* offline or a stale build: stay in English */
  }
}

export function setLanguage(code: LanguageCode | '') {
  try {
    if (code) localStorage.setItem(KEY, code);
    else localStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
  cached = code || browserLanguage();
  applyLanguage();
  const language = cached;
  if (loaded[language]) window.dispatchEvent(new Event(CHANGED));
  else void loadLanguage(language).then(() => window.dispatchEvent(new Event(CHANGED)));
}

/** Tell the browser (and screen readers) which language the page is in. */
export function applyLanguage() {
  if (typeof document !== 'undefined') document.documentElement.lang = getLanguage();
}

const shown = (v: Vars[string]) => (v === false || v === true || v == null ? '' : String(v));

/** Values for {placeholders}; like JSX, false, null and undefined show nothing. */
export type Vars = Record<string, string | number | boolean | null | undefined>;

export function translate(language: LanguageCode, key: MessageKey, vars?: Vars): string {
  const messages: Messages = loaded[language] ?? en;
  const text = messages[key] ?? en[key] ?? key;
  return vars ? text.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? shown(vars[name]) : whole)) : text;
}

function subscribe(notify: () => void) {
  // Another tab changed the language: forget the cached one first.
  const fromStorage = () => {
    cached = null;
    applyLanguage();
    void loadLanguage().then(notify);
  };
  window.addEventListener(CHANGED, notify);
  window.addEventListener('storage', fromStorage);
  return () => {
    window.removeEventListener(CHANGED, notify);
    window.removeEventListener('storage', fromStorage);
  };
}

export const useLanguage = () => useSyncExternalStore(subscribe, getLanguage, getLanguage);

/** `t('nav.inbox')` in the current language; re-renders when it changes. */
export function useT() {
  const language = useLanguage();
  return useCallback(
    (key: MessageKey, vars?: Vars) => translate(language, key, vars),
    [language],
  );
}

/**
 * `tr('ui.agents.import')` in the current language. Unlike useT() it works
 * anywhere (event handlers, helpers, getters); screens pick up a language
 * change because <LanguageRoot> renders the app afresh when it changes.
 */
export const tr = (key: MessageKey, vars?: Vars): string => translate(getLanguage(), key, vars);

/**
 * Pick the singular or plural text for a count, by the language's own rules
 * (French uses the singular for 0 too): trn(n, 'ui.x.file_one', 'ui.x.file_other').
 * The count is available to both texts as {n}.
 */
export function trn(n: number, one: MessageKey, other: MessageKey, vars?: Vars): string {
  const language = getLanguage();
  let form: Intl.LDMLPluralRule = n === 1 ? 'one' : 'other';
  try {
    form = new Intl.PluralRules(language).select(n);
  } catch {
    /* very old browser: English rule */
  }
  return translate(language, form === 'one' ? one : other, { n, ...vars });
}

/** Wraps the app so every screen redraws in the new language when it changes. */
export function LanguageRoot({ children }: { children: ReactNode }) {
  const language = useLanguage();
  return createElement(Fragment, { key: language }, children);
}

export type { MessageKey };
