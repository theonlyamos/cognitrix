import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { API_BASE } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import MarkdownMessage from '@/components/MarkdownMessage';
import { desktop, onDesktopEvent } from '@/lib/desktop';

interface AgentLite {
  id: string;
  name: string;
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

/**
 * The Alt+Space window: one question to one agent, answered in place. Ctrl
 * Enter (or "Open in full chat") continues the conversation in the main window.
 */
export default function QuickAsk() {
  const { data: agents } = useResource<AgentLite[]>('/agents');
  const [agentId, setAgentId] = useState(() => localStorage.getItem('selectedAgentId') || '');
  const [question, setQuestion] = useState('');
  const [asked, setAsked] = useState('');
  const [answer, setAnswer] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!agentId && agents?.length) setAgentId(agents[0].id);
  }, [agents, agentId]);

  useEffect(() => {
    inputRef.current?.focus();
    let stop: (() => void) | undefined;
    void onDesktopEvent('quick-ask-open', () => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }).then((fn) => (stop = fn));
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') void desktop.hideQuickAsk();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      stop?.();
      document.removeEventListener('keydown', onKey);
    };
  }, []);

  const openInChat = () => {
    if (!agentId) return;
    try {
      localStorage.setItem('selectedAgentId', agentId);
      if (sessionId) localStorage.setItem(`chatSession:${agentId}`, sessionId);
    } catch {
      /* ignore */
    }
    void desktop.hideQuickAsk();
    void desktop.showMain('/home');
  };

  const ask = async (e?: FormEvent) => {
    e?.preventDefault();
    const message = question.trim();
    if (!message || !agentId || busy) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setError(null);
    setAnswer('');
    setAsked(message);
    setQuestion('');
    try {
      const response = await fetch(`${API_BASE}/agents/${agentId}/generate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          Authorization: `Bearer ${localStorage.getItem('token') ?? ''}`,
        },
        body: JSON.stringify({ message, stream: true, session_id: sessionId }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${response.status})`);
      }
      await readSse(response, (event, data) => {
        let payload: Record<string, string> = {};
        try {
          payload = JSON.parse(data);
        } catch {
          return;
        }
        if (event === 'chunk' && payload.content) setAnswer((a) => a + payload.content);
        else if (event === 'error') setError(payload.detail || 'Something went wrong.');
        if (payload.session_id) setSessionId(payload.session_id);
      });
    } catch (err) {
      if ((err as Error).name !== 'AbortError') setError((err as Error).message || 'Could not reach the engine.');
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      openInChat();
    }
  };

  const agentName = agents?.find((a) => a.id === agentId)?.name;

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-panel text-fg">
      <form onSubmit={ask} data-tauri-drag-region className="flex flex-none items-center gap-3 px-4 py-3.5">
        <span className="grid h-7 w-7 flex-none place-items-center rounded-lg bg-accent text-accent-foreground">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
        </span>
        <label htmlFor="quick-ask-input" className="sr-only">Ask Cognitrix</label>
        <input
          id="quick-ask-input"
          ref={inputRef}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={asked ? 'Ask a follow-up…' : 'Ask anything…'}
          autoComplete="off"
          className="min-w-0 flex-1 border-0 bg-transparent text-[17px] outline-none placeholder:text-fg-dim"
        />
        <label htmlFor="quick-ask-agent" className="sr-only">Agent</label>
        <select
          id="quick-ask-agent"
          value={agentId}
          onChange={(e) => {
            setAgentId(e.target.value);
            setSessionId(null);
          }}
          className="h-7 max-w-[160px] rounded-full border-0 bg-panel-2 px-3 text-[12px] text-fg outline-none"
        >
          {(agents ?? []).map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
        <kbd className="hidden rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim sm:inline">Alt Space</kbd>
      </form>

      <div className="min-h-0 flex-1 overflow-y-auto border-t border-line px-4 py-3.5 pl-[58px] text-[14px] leading-relaxed" aria-live="polite">
        {!asked && !error && (
          <p className="text-fg-dim">
            {agents && agents.length === 0 ? 'Create an agent in the app first, then ask it anything here.' : `Ask ${agentName ?? 'your agent'} a quick question. Tools run the same as in a chat.`}
          </p>
        )}
        {asked && <p className="mb-2 font-medium">{asked}</p>}
        {answer && <MarkdownMessage content={answer} />}
        {busy && !answer && <p className="text-fg-dim">Thinking…</p>}
        {error && <p role="alert" className="text-danger-ink">{error}</p>}
      </div>

      <div className="flex flex-none gap-5 bg-bg/60 px-4 py-2 pl-[58px] font-mono text-[11px] text-fg-dim">
        <span>Enter ask</span>
        <button type="button" onClick={openInChat} className="hover:text-fg">Ctrl Enter open in full chat</button>
        <span>Esc dismiss</span>
      </div>
    </div>
  );
}
