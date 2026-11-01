import { useEffect, useState } from 'react';

// Per-device UI preferences, kept in localStorage. Each one is read again
// when another tab or the Settings page changes it.

export type SendKey = 'enter' | 'ctrl-enter';

const SEND_KEY = 'sendKey';
const REDUCE_MOTION = 'reduceMotion';
const CHANGED = 'cognitrix:preference-changed';

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
  window.dispatchEvent(new CustomEvent(CHANGED, { detail: key }));
}

function usePreference<T>(key: string, parse: (raw: string | null) => T): T {
  const [value, setValue] = useState(() => parse(read(key)));
  useEffect(() => {
    const refresh = () => setValue(parse(read(key)));
    const onStorage = (e: StorageEvent) => { if (e.key === key) refresh(); };
    const onChange = (e: Event) => { if ((e as CustomEvent).detail === key) refresh(); };
    window.addEventListener('storage', onStorage);
    window.addEventListener(CHANGED, onChange);
    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener(CHANGED, onChange);
    };
  }, [key, parse]);
  return value;
}

const parseSendKey = (raw: string | null): SendKey => (raw === 'ctrl-enter' ? 'ctrl-enter' : 'enter');
const parseBool = (raw: string | null) => raw === 'true';

export const getSendKey = () => parseSendKey(read(SEND_KEY));
export const useSendKey = () => usePreference(SEND_KEY, parseSendKey);
export const setSendKey = (v: SendKey) => write(SEND_KEY, v === 'enter' ? null : v);

/** True when this key press should send the message (not add a new line). */
export function isSendKey(e: { key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }, mode: SendKey): boolean {
  if (e.key !== 'Enter' || e.shiftKey) return false;
  const withModifier = e.ctrlKey || e.metaKey;
  return mode === 'enter' ? true : withModifier;
}

export const useReduceMotion = () => usePreference(REDUCE_MOTION, parseBool);
export function setReduceMotion(on: boolean) {
  document.documentElement.classList.toggle('reduce-motion', on);
  write(REDUCE_MOTION, on ? 'true' : null);
}
