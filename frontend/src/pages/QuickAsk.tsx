import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { API_BASE, browserTimezone } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import MarkdownMessage from '@/components/MarkdownMessage';
import { desktop, isDesktop, onDesktopEvent } from '@/lib/desktop';
import { Waveform, useVoiceInput } from '@/components/VoiceInput';
import { VoiceActivityDetector } from '@/lib/vad';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

interface AgentLite {
  id: string;
  name: string;
}

interface Turn {
  id: number;
  question: string;
  answer: string;
  error: string | null;
  busy: boolean;
}

export type QuickAskMode = 'voice' | 'keyboard';

const MODE_KEY = 'quickAskMode';
/** Silence after speech that sends the question. */
const PAUSE_MS = 1000;
/** Listening this long without hearing anyone stops the mic. */
const IDLE_MS = 45_000;

function readQuickAskMode(): QuickAskMode {
  try {
    return localStorage.getItem(MODE_KEY) === 'keyboard' ? 'keyboard' : 'voice';
  } catch {
    return 'voice';
  }
}

function saveQuickAskMode(mode: QuickAskMode) {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* ignore */
  }
}

/** The quick ask window is see-through on Windows and Linux (macOS keeps an opaque window). */
function seeThrough(): boolean {
  return isDesktop() && !/Mac/i.test(navigator.userAgent);
}

/** Read an SSE response body, calling onEvent for each (event, data) pair. */
async function readSse(response: Response, onEvent: (event: string, data: string) => void) {
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    let split = buffer.indexOf('\n\n');
    while (split !== -1) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let event = 'message';
      const data: string[] = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent(event, data.join('\n'));
      split = buffer.indexOf('\n\n');
    }
  }
}

const KeyboardIcon = () => (
  <svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><rect x="2.5" y="6" width="19" height="12" rx="2.5" /><path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17 10h.01M7.5 14h9" /></svg>
);
const MicIcon = () => (
  <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
);

/** Three bouncing dots: transcribing, then waiting for the first words of the answer. */
function ThinkingDots() {
  return (
    <div className="flex flex-1 items-center justify-center gap-1.5" role="status" aria-label={tr('ui.quickAsk.thinking')}>
      {[0, 150, 300].map((delay) => (
        <i key={delay} className="quick-ask-dot block h-[7px] w-[7px] rounded-full bg-accent-ink" style={{ animationDelay: `${delay}ms` }} />
      ))}
    </div>
  );
}

/**
 * The Alt+Space window, voice first: a pill docked above the taskbar listens
 * straight away and sends what you say when you pause; answers stack as cards
 * above it. The keyboard button (or Tab, or just typing) switches to a text
 * box, and the last mode is remembered. Ctrl Enter continues in the main window.
 */
export default function QuickAsk() {
  const { data: agents } = useResource<AgentLite[]>('/agents');
  const [agentId, setAgentId] = useState(() => localStorage.getItem('selectedAgentId') || '');
  const [mode, setModeState] = useState<QuickAskMode>(readQuickAskMode);
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [opened, setOpened] = useState<Set<number>>(new Set());
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  // The desktop window loads hidden; the mic only opens while it is showing.
  const [shown, setShown] = useState(() => !isDesktop() || document.hasFocus());
  // Voice mode, not listening: nobody spoke for a while, or a voice error.
  const [paused, setPaused] = useState(false);
  const [stopping, setStopping] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const cardsRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const turnId = useRef(0);
  const vad = useRef<VoiceActivityDetector | null>(null);
  const heard = useRef(false);
  const idleMs = useRef(0);
  const askRef = useRef<(message: string) => void>(() => undefined);
  const stoppingRef = useRef(false);
  const busy = turns.some((t) => t.busy);

  const voice = useVoiceInput({
    keys: false,
    onText: (text) => askRef.current(text),
    onBlock: (block, rate) => {
      if (stoppingRef.current) return;
      if (!vad.current) vad.current = new VoiceActivityDetector({ sampleRate: rate, pauseMs: PAUSE_MS });
      const event = vad.current.push(block);
      if (event?.type === 'speech-start') heard.current = true;
      if (event?.type === 'speech-end') {
        stoppingRef.current = true;
        setStopping(true);
        void voiceRef.current.stop().finally(() => {
          stoppingRef.current = false;
          setStopping(false);
        });
        return;
      }
      if (!heard.current && !vad.current.active) {
        idleMs.current += (block.length / rate) * 1000;
        if (idleMs.current >= IDLE_MS) {
          voiceRef.current.cancel();
          setPaused(true);
        }
      }
    },
  });
  const voiceRef = useRef(voice);
  voiceRef.current = voice;

  // Voice can't be used until a speech-to-text provider is set up: type instead.
  // Without a speech-to-text provider the pill stays, offering to set one up
  // (switching to the keyboard on its own looked like voice mode was broken).
  const effectiveMode: QuickAskMode = mode;
  const listening = effectiveMode === 'voice' && voice.status === 'recording' && !stopping;

  useEffect(() => {
    if (!agentId && agents?.length) setAgentId(agents[0].id);
  }, [agents, agentId]);

  // See-through page so the pill and cards float on the desktop.
  useEffect(() => {
    if (!seeThrough()) return;
    document.documentElement.classList.add('quick-ask-window');
    return () => document.documentElement.classList.remove('quick-ask-window');
  }, []);

  // Listen whenever voice mode is showing and idle: on open, and again after each answer.
  useEffect(() => {
    if (effectiveMode !== 'voice' || !shown || paused || busy || stopping) return;
    if (voice.status !== 'idle' || voice.error || voice.needsSetup) return;
    vad.current = null;
    heard.current = false;
    idleMs.current = 0;
    void voice.start();
  }, [effectiveMode, shown, paused, busy, stopping, voice]);

  // A voice error (mic blocked, nothing heard, transcription failed) stops listening until a tap.
  useEffect(() => {
    if (voice.error) setPaused(true);
  }, [voice.error]);

  const setMode = useCallback((next: QuickAskMode) => {
    setModeState(next);
    saveQuickAskMode(next);
    setMenuOpen(false);
    if (next === 'keyboard') {
      voiceRef.current.cancel();
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      voiceRef.current.dismiss();
      setPaused(false);
    }
  }, []);

  const resume = () => {
    voice.dismiss();
    setPaused(false);
  };

  const hide = useCallback(() => {
    voiceRef.current.cancel();
    setMenuOpen(false);
    if (isDesktop()) {
      setShown(false);
      void desktop.hideQuickAsk();
    }
  }, []);

  const openInChat = useCallback(
    (path = '/home') => {
      if (!agentId) return;
      try {
        localStorage.setItem('selectedAgentId', agentId);
        if (sessionId) localStorage.setItem(`chatSession:${agentId}`, sessionId);
      } catch {
        /* ignore */
      }
      hide();
      void desktop.showMain(path);
    },
    [agentId, sessionId, hide],
  );

  const openVoiceSettings = () => {
    hide();
    void desktop.showMain('/settings/voice');
  };

  useEffect(() => {
    const stops: Array<() => void> = [];
    void onDesktopEvent('quick-ask-open', () => {
      setShown(true);
      setPaused(false);
      voiceRef.current.dismiss();
      if (readQuickAskMode() === 'keyboard') {
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    }).then((fn) => stops.push(fn));
    void onDesktopEvent('quick-ask-hide', () => {
      setShown(false);
      voiceRef.current.cancel();
    }).then((fn) => stops.push(fn));
    // The open event can be missed while the page is (re)loading; focus means it's up.
    const onFocus = () => setShown(true);
    // The desktop hides quick ask when it loses focus; never keep listening then.
    const onBlur = () => {
      if (!isDesktop()) return;
      setShown(false);
      voiceRef.current.cancel();
    };
    window.addEventListener('focus', onFocus);
    window.addEventListener('blur', onBlur);
    return () => {
      stops.forEach((fn) => fn());
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('blur', onBlur);
    };
  }, []);

  useEffect(() => {
    if (effectiveMode === 'keyboard') inputRef.current?.focus();
  }, [effectiveMode]);

  // Keys that work anywhere in the window.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (menuOpen) setMenuOpen(false);
        else hide();
        return;
      }
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        openInChat();
        return;
      }
      if (effectiveMode !== 'voice') return;
      if (e.target instanceof Element && e.target.closest('select, input, textarea')) return;
      if (e.key === 'Enter' && voiceRef.current.status === 'recording' && heard.current) {
        // Send now instead of waiting for the pause.
        e.preventDefault();
        stoppingRef.current = true;
        setStopping(true);
        void voiceRef.current.stop().finally(() => {
          stoppingRef.current = false;
          setStopping(false);
        });
      } else if (e.key === 'Tab') {
        e.preventDefault();
        setMode('keyboard');
      } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== ' ') {
        // Just start typing: the letter lands in the box.
        e.preventDefault();
        setQuestion((q) => q + e.key);
        setMode('keyboard');
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [effectiveMode, menuOpen, hide, openInChat, setMode]);

  const updateTurn = (id: number, patch: (t: Turn) => Partial<Turn>) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...patch(t) } : t)));

  const ask = async (text: string) => {
    const message = text.trim();
    if (!message || !agentId || busy) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const id = ++turnId.current;
    setTurns((all) => [...all, { id, question: message, answer: '', error: null, busy: true }]);
    setQuestion('');
    try {
      const send = (session: string | null) => fetch(`${API_BASE}/agents/${agentId}/generate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          Authorization: `Bearer ${localStorage.getItem('token') ?? ''}`,
          ...(browserTimezone() ? { 'X-Timezone': browserTimezone() } : {}),
        },
        body: JSON.stringify({ message, stream: true, session_id: session }),
        signal: controller.signal,
      });
      let response = await send(sessionId);
      // The quick ask chat was deleted (or belongs to another agent now):
      // start a new one instead of failing every question until a restart.
      if (response.status === 404 && sessionId) {
        setSessionId(null);
        response = await send(null);
      }
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || tr('ui.quickAsk.requestFailedStatus', { status: response.status }));
      }
      await readSse(response, (event, data) => {
        let payload: Record<string, string> = {};
        try {
          payload = JSON.parse(data);
        } catch {
          return;
        }
        if (event === 'chunk' && payload.content) updateTurn(id, (t) => ({ answer: t.answer + payload.content }));
        else if (event === 'error') updateTurn(id, () => ({ error: payload.detail || tr('ui.quickAsk.somethingWentWrong') }));
        if (payload.session_id) setSessionId(payload.session_id);
      });
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        updateTurn(id, () => ({ error: (err as Error).message || tr('ui.quickAsk.couldNotReachTheEngine') }));
      }
    } finally {
      updateTurn(id, () => ({ busy: false }));
    }
  };
  askRef.current = (message) => void ask(message);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void ask(question);
  };

  const onInputKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      openInChat();
    }
  };

  // The window is as tall as the pill and cards, and stays docked at the bottom.
  useLayoutEffect(() => {
    const content = contentRef.current;
    const cards = cardsRef.current;
    if (!content || !cards || !isDesktop()) return;
    let last = 0;
    const report = () => {
      const style = getComputedStyle(content);
      const pad = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      const gap = parseFloat(style.rowGap) || 0;
      const bottom = content.lastElementChild as HTMLElement | null;
      // The agent menu opens above the pill; make room for it when the cards are shorter.
      const menu = menuRef.current?.offsetHeight ?? 0;
      const above = Math.max(cards.scrollHeight + gap, menu ? menu + 12 : 0);
      const height = Math.ceil(above + (bottom?.offsetHeight ?? 0) + pad);
      if (Math.abs(height - last) < 1) return;
      last = height;
      void desktop.fitQuickAsk(height);
    };
    const observer = new ResizeObserver(report);
    observer.observe(cards);
    if (content.lastElementChild) observer.observe(content.lastElementChild);
    if (menuRef.current) observer.observe(menuRef.current);
    report();
    return () => observer.disconnect();
  }, [menuOpen]);

  // Keep the newest answer in view as it streams in.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const agentName = agents?.find((a) => a.id === agentId)?.name ?? '';
  const latest = turns[turns.length - 1];
  const waiting = latest?.busy && !latest.answer && !latest.error;
  const thinking = waiting || voice.status === 'transcribing' || stopping;
  const noAgents = agents && agents.length === 0;

  const hints = (
    <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-line bg-bg/60 px-5 py-2 font-mono text-[11px] text-fg-dim">
      <span>{effectiveMode === 'voice' ? tr('ui.quickAsk.speakToFollowUp') : tr('ui.quickAsk.enterAsk')}</span>
      <button type="button" onClick={() => openInChat()} className="hover:text-fg">{tr('ui.quickAsk.ctrlEnterOpenInFull')}</button>
      <span>{tr('ui.quickAsk.escDismiss')}</span>
    </div>
  );

  const avatar = (
    <div className="relative flex-none">
      <button
        type="button"
        onClick={() => setMenuOpen((open) => !open)}
        aria-haspopup="listbox"
        aria-expanded={menuOpen}
        aria-label={tr('ui.quickAsk.changeAgent', { agentName })}
        title={agentName}
        className="grid h-9 w-9 place-items-center rounded-full bg-accent text-[14px] font-bold text-accent-foreground transition hover:brightness-105"
      >
        {(agentName || '?').slice(0, 1).toUpperCase()}
      </button>
      {menuOpen && (
        <ul
          ref={menuRef}
          role="listbox"
          aria-label={tr('ui.quickAsk.agent')}
          className="absolute bottom-12 left-0 z-10 max-h-64 w-56 overflow-y-auto rounded-xl border border-line bg-panel p-1 shadow-xl"
        >
          {(agents ?? []).map((a) => (
            <li key={a.id}>
              <button
                type="button"
                role="option"
                aria-selected={a.id === agentId}
                onClick={() => {
                  setMenuOpen(false);
                  if (a.id === agentId) return;
                  abortRef.current?.abort();
                  setAgentId(a.id);
                  setSessionId(null);
                  setTurns([]);
                }}
                className={cn(
                  'flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-panel-2',
                  a.id === agentId && 'bg-panel-2 font-medium',
                )}
              >
                <span className="grid h-5 w-5 flex-none place-items-center rounded-full bg-accent text-[10px] font-bold text-accent-foreground">{a.name.slice(0, 1).toUpperCase()}</span>
                <span className="truncate">{a.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );

  // What sits right above the pill besides the answer cards.
  let note: { text: string; tone: 'dim' | 'danger'; action?: () => void } | null = null;
  if (noAgents) note = { text: tr('ui.quickAsk.createAnAgentInThe'), tone: 'dim' };
  else if (effectiveMode === 'voice' && voice.error) note = { text: voice.error, tone: 'danger' };

  return (
    <div className="flex h-dvh flex-col justify-end overflow-hidden text-fg">
      <div ref={contentRef} className="flex max-h-full min-h-0 flex-col items-center gap-2.5 px-5 pb-3 pt-4">
        <div ref={scrollRef} className="min-h-0 w-full overflow-y-auto">
          <div ref={cardsRef} className="flex w-full flex-col items-center gap-2.5" aria-live="polite">
            {turns.map((turn, index) => {
              const newest = index === turns.length - 1;
              if (newest && waiting) {
                return (
                  <p key={turn.id} className="max-w-full truncate rounded-full border border-line bg-panel px-3.5 py-1.5 text-[13px] text-fg-dim shadow-md">
                    “{turn.question}”
                  </p>
                );
              }
              if (!newest && !opened.has(turn.id)) {
                return (
                  <button
                    key={turn.id}
                    type="button"
                    onClick={() => setOpened((s) => new Set(s).add(turn.id))}
                    className="flex w-full items-center gap-3 rounded-2xl border border-line bg-panel px-5 py-2.5 text-left text-[13px] opacity-75 shadow-md transition hover:opacity-100"
                  >
                    <b className="min-w-0 flex-1 truncate font-semibold">{turn.question}</b>
                    <span className="flex-none font-mono text-[11px] text-fg-dim">{tr('ui.quickAsk.tapToOpen')}</span>
                  </button>
                );
              }
              return (
                <article key={turn.id} className="w-full overflow-hidden rounded-2xl border border-line bg-panel shadow-lg">
                  <div className="px-5 py-3.5 text-[14px] leading-relaxed">
                    <p className="mb-2 font-semibold">{turn.question}</p>
                    {turn.answer && <MarkdownMessage content={turn.answer} />}
                    {turn.busy && !turn.answer && !turn.error && <p className="text-fg-dim">{tr('ui.quickAsk.thinking')}</p>}
                    {turn.error && <p role="alert" className="text-danger-ink">{turn.error}</p>}
                  </div>
                  {newest && hints}
                </article>
              );
            })}
            {note && (
              <p
                role="alert"
                className={cn(
                  'max-w-full rounded-full border border-line bg-panel px-3.5 py-1.5 text-[12.5px] shadow-md',
                  note.tone === 'danger' ? 'text-danger-ink' : 'text-fg-dim',
                )}
              >
                {note.text}
                {note.action && (
                  <button type="button" onClick={note.action} className="ml-2 text-accent-ink hover:underline">{tr('ui.voiceInput.setItUpInSettings')}</button>
                )}
              </p>
            )}
          </div>
        </div>

        <div className="flex w-full flex-none justify-center">
          {effectiveMode === 'voice' ? (
            <div data-tauri-drag-region className="flex h-14 w-[420px] max-w-full items-center gap-2.5 rounded-full border border-line bg-panel pl-2.5 pr-2 shadow-xl">
              {avatar}
              {thinking ? (
                <ThinkingDots />
              ) : voice.needsSetup ? (
                <button
                  type="button"
                  onClick={openVoiceSettings}
                  title={tr('ui.quickAsk.voiceNeedsSetup')}
                  className="h-10 min-w-0 flex-1 truncate rounded-full px-3 text-[13px] text-fg-dim hover:bg-panel-2 hover:text-fg"
                >
                  {tr('ui.quickAsk.setUpVoice')}
                </button>
              ) : listening ? (
                <div className="min-w-0 flex-1" role="status" aria-label={tr('ui.quickAsk.listening')}>
                  <Waveform analyser={voice.analyser} className="h-8 text-accent-ink" />
                </div>
              ) : (
                <button
                  type="button"
                  onClick={resume}
                  disabled={voice.status === 'starting'}
                  className="h-10 min-w-0 flex-1 truncate rounded-full px-3 text-[13px] text-fg-dim hover:bg-panel-2 hover:text-fg"
                >
                  {voice.status === 'starting' ? tr('ui.quickAsk.listening') : tr('ui.quickAsk.tapToTalk')}
                </button>
              )}
              <button
                type="button"
                onClick={() => setMode('keyboard')}
                aria-label={tr('ui.quickAsk.typeInstead')}
                title={tr('ui.quickAsk.typeInstead')}
                className="grid h-[38px] w-[38px] flex-none place-items-center rounded-full bg-panel-2 text-fg transition hover:brightness-110"
              >
                <KeyboardIcon />
              </button>
            </div>
          ) : (
            <form onSubmit={onSubmit} data-tauri-drag-region className="flex h-14 w-full items-center gap-2.5 rounded-2xl border border-line bg-panel pl-2.5 pr-2 shadow-xl">
              {avatar}
              <label htmlFor="quick-ask-input" className="sr-only">{tr('ui.quickAsk.askCognitrix')}</label>
              <input
                id="quick-ask-input"
                ref={inputRef}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={onInputKey}
                placeholder={turns.length ? tr('ui.quickAsk.askAFollowUp') : agentName ? tr('ui.quickAsk.askAgentAnything', { agentName }) : tr('ui.quickAsk.askAnything')}
                autoComplete="off"
                className="min-w-0 flex-1 border-0 bg-transparent text-[17px] outline-none placeholder:text-fg-dim"
              />
              <button
                type="button"
                onClick={() => setMode('voice')}
                aria-label={tr('ui.quickAsk.talkInstead')}
                title={tr('ui.quickAsk.talkInstead')}
                className="grid h-[38px] w-[38px] flex-none place-items-center rounded-full bg-accent text-accent-foreground transition hover:brightness-105"
              >
                <MicIcon />
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
