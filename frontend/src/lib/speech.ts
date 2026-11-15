import { useSyncExternalStore } from 'react';
import { api } from '@/lib/api';

// Spoken replies. One thing is read at a time, by a speech service through
// the engine (OpenAI or Gemini) or by the voices built into the browser.
// The choices are per device, kept in localStorage.

export interface SpeechProviderOption {
  id: string;
  label: string;
  voices: string[];
  /** A key for it is saved, here or on the server. */
  ready: boolean;
}

export interface SpeechOptions {
  max_chars: number;
  providers: SpeechProviderOption[];
}

export interface SpeechPrefs {
  /** '' picks a ready service, or the browser when there is none. */
  provider: string;
  voice: string;
  autoRead: boolean;
}

const KEY = 'speechPrefs';
const CHANGED = 'cognitrix:speech-prefs';
const DEFAULTS: SpeechPrefs = { provider: '', voice: '', autoRead: false };
const PIECE_CHARS = 1200;

let cachedRaw: string | null | undefined;
let cachedPrefs: SpeechPrefs = DEFAULTS;

export function getSpeechPrefs(): SpeechPrefs {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(KEY);
  } catch {
    /* storage blocked */
  }
  if (raw === cachedRaw) return cachedPrefs;
  cachedRaw = raw;
  try {
    const data = raw ? JSON.parse(raw) : {};
    cachedPrefs = {
      provider: typeof data.provider === 'string' ? data.provider : '',
      voice: typeof data.voice === 'string' ? data.voice : '',
      autoRead: data.autoRead === true,
    };
  } catch {
    cachedPrefs = DEFAULTS;
  }
  return cachedPrefs;
}

export function setSpeechPrefs(patch: Partial<SpeechPrefs>) {
  const next = { ...getSpeechPrefs(), ...patch };
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    cachedRaw = JSON.stringify(next);
    cachedPrefs = next;
  }
  window.dispatchEvent(new Event(CHANGED));
}

function subscribePrefs(notify: () => void) {
  window.addEventListener(CHANGED, notify);
  window.addEventListener('storage', notify);
  return () => {
    window.removeEventListener(CHANGED, notify);
    window.removeEventListener('storage', notify);
  };
}

export const useSpeechPrefs = () => useSyncExternalStore(subscribePrefs, getSpeechPrefs, getSpeechPrefs);

export async function getSpeechOptions(): Promise<SpeechOptions> {
  return (await api.get<SpeechOptions>('/audio/speech/options')).data;
}

export const browserSpeechAvailable = () =>
  typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined';

/** What a person would read aloud: no code, markup, links' addresses or reasoning. */
export function speakableText(markdown: string): string {
  return markdown
    .replace(/<think>[\s\S]*?<\/think>/g, ' ')
    .replace(/```[\s\S]*?(```|$)/g, ' (code omitted) ')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' link ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\|?[\s:|-]{3,}\|?\s*$/gm, '')
    .replace(/\|/g, ', ')
    .replace(/(\*\*|__|\*|_|~~)(?=\S)([\s\S]*?\S)\1/g, '$2')
    .replace(/<[^>]+>/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .trim();
}

/** Cut text into pieces short enough to start playing quickly, at sentence ends. */
export function speechPieces(text: string, size = PIECE_CHARS): string[] {
  const pieces: string[] = [];
  let rest = text.trim();
  while (rest.length > size) {
    const window = rest.slice(0, size);
    let cut = Math.max(window.lastIndexOf('. '), window.lastIndexOf('? '), window.lastIndexOf('! '), window.lastIndexOf('\n'));
    if (cut < size * 0.4) cut = window.lastIndexOf(' ');
    if (cut <= 0) cut = size - 1;
    pieces.push(rest.slice(0, cut + 1).trim());
    rest = rest.slice(cut + 1).trim();
  }
  if (rest) pieces.push(rest);
  return pieces;
}

// ------------------------------------------------------------------ playback

export interface SpeechState {
  /** What is being read (a message id), or null. */
  id: string | null;
  phase: 'idle' | 'loading' | 'playing';
  error: string | null;
}

let state: SpeechState = { id: null, phase: 'idle', error: null };
const listeners = new Set<() => void>();
let run = 0;
let audio: HTMLAudioElement | null = null;
let abort: AbortController | null = null;

function set(next: SpeechState) {
  state = next;
  listeners.forEach((fn) => fn());
}

const subscribeState = (notify: () => void) => {
  listeners.add(notify);
  return () => { listeners.delete(notify); };
};
const snapshot = () => state;

export const useSpeechState = () => useSyncExternalStore(subscribeState, snapshot, snapshot);

export function stopSpeaking() {
  run += 1;
  abort?.abort();
  abort = null;
  if (audio) {
    audio.pause();
    audio = null;
  }
  if (browserSpeechAvailable()) window.speechSynthesis.cancel();
  if (state.phase !== 'idle' || state.error) set({ id: null, phase: 'idle', error: null });
}

function playBlob(blob: Blob, mine: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const player = new Audio(url);
    audio = player;
    const done = (err?: unknown) => {
      URL.revokeObjectURL(url);
      if (err) reject(err); else resolve();
    };
    player.onended = () => done();
    player.onpause = () => { if (mine !== run) done(); };
    player.onerror = () => done(new Error('The audio could not be played.'));
    player.play().catch(done);
  });
}

async function fetchPiece(text: string, provider: string, voice: string, signal: AbortSignal): Promise<Blob> {
  const res = await api.post('/audio/speech', { text, provider, voice }, { responseType: 'blob', signal, timeout: 120_000 });
  return res.data as Blob;
}

function speakWithBrowser(pieces: string[], voiceName: string, mine: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const synth = window.speechSynthesis;
    const voice = synth.getVoices().find((v) => v.name === voiceName);
    let index = 0;
    const next = () => {
      if (mine !== run) return resolve();
      if (index >= pieces.length) return resolve();
      const utterance = new SpeechSynthesisUtterance(pieces[index++]);
      if (voice) utterance.voice = voice;
      // Some browsers queue speech that never starts (no voices installed,
      // headless): give up rather than wait forever.
      const watchdog = window.setTimeout(() => {
        synth.cancel();
        reject(new Error('The browser could not read this aloud.'));
      }, 5000);
      utterance.onstart = () => window.clearTimeout(watchdog);
      utterance.onend = () => { window.clearTimeout(watchdog); next(); };
      utterance.onerror = (e) => {
        window.clearTimeout(watchdog);
        if (e.error === 'canceled' || e.error === 'interrupted') resolve();
        else reject(new Error('The browser could not read this aloud.'));
      };
      synth.speak(utterance);
    };
    next();
  });
}

async function describe(err: unknown): Promise<string> {
  // Errors for a blob request arrive as a blob too.
  const data = (err as { response?: { data?: unknown } })?.response?.data;
  if (data instanceof Blob) {
    try {
      const detail = JSON.parse(await data.text())?.detail;
      if (typeof detail === 'string') return detail;
    } catch {
      /* not JSON */
    }
  }
  if (err instanceof DOMException && err.name === 'NotAllowedError') {
    return 'The browser blocked the sound. Press listen on the reply to hear it.';
  }
  return err instanceof Error && err.message ? err.message : 'Could not read this aloud.';
}

/** Read `markdown` aloud. Reading something else, or the same thing again, stops what was playing.
 *  When the service picked automatically fails (no credit, rate limit), the
 *  next ready one is tried, then the browser's own voices. */
export async function speakText(id: string, markdown: string, options?: SpeechOptions | null): Promise<void> {
  stopSpeaking();
  const text = speakableText(markdown);
  if (!text) return;
  const mine = ++run;
  const prefs = getSpeechPrefs();
  let order: string[] = ['browser'];
  if (prefs.provider !== 'browser') {
    let known = options;
    if (!known) {
      try {
        known = await getSpeechOptions();
      } catch {
        known = null;
      }
      if (mine !== run) return;
    }
    const ready = (known?.providers.filter((p) => p.ready) ?? []).map((p) => p.id);
    if (prefs.provider && ready.includes(prefs.provider)) {
      // A service the person chose: no quiet switching to another one.
      order = [prefs.provider];
    } else if (!prefs.provider) {
      order = [...ready, 'browser'];
    }
  }

  set({ id, phase: 'loading', error: null });
  let lastError: unknown = null;
  for (const provider of order) {
    if (provider === 'browser' && !browserSpeechAvailable()) {
      lastError = lastError ?? new Error('This browser has no voices. Add an OpenAI or Gemini key under Settings, Voice.');
      continue;
    }
    let started = false;
    try {
      if (provider === 'browser') {
        set({ id, phase: 'playing', error: null });
        started = true;
        await speakWithBrowser(speechPieces(text, 220), prefs.provider === 'browser' ? prefs.voice : '', mine);
      } else {
        const controller = new AbortController();
        abort = controller;
        const pieces = speechPieces(text);
        const voice = prefs.provider === provider ? prefs.voice : '';
        // Ask for the next piece while the current one plays, so there's no gap.
        let pending = fetchPiece(pieces[0], provider, voice, controller.signal);
        for (let i = 0; i < pieces.length; i += 1) {
          const blob = await pending;
          if (mine !== run) return;
          if (i + 1 < pieces.length) {
            pending = fetchPiece(pieces[i + 1], provider, voice, controller.signal);
            pending.catch(() => {});
          }
          set({ id, phase: 'playing', error: null });
          started = true;
          await playBlob(blob, mine);
          if (mine !== run) return;
        }
      }
      if (mine === run) set({ id: null, phase: 'idle', error: null });
      return;
    } catch (err) {
      if (mine !== run) return;
      lastError = err;
      // Halfway through a reply, starting over in another voice would repeat it.
      if (started) break;
      set({ id, phase: 'loading', error: null });
    }
  }
  if (mine !== run) return;
  set({ id, phase: 'idle', error: await describe(lastError) });
}
