import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSession } from '@/context/SessionContext';
import { useSSE } from '@/hooks/useSSE';
import { useResource } from '@/hooks/useResource';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { parseChatEntries, toChatMessages, fmtRelative } from '@/lib/transcript';

const SUGGESTIONS = [
  'Summarize the benefits of unit testing.',
  'First research X, then write a short brief.',
  'What can you help me with?',
];

const fmtTime = (t?: string | number) =>
  t ? new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '';

// Render markdown-style links ([label](/path)) as real links — multi-step
// replies carry a "View task run" link to the run page.
const MD_LINK = /\[([^\]]+)\]\((\/[^\s)]+)\)/g;
function renderWithLinks(content: string) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const m of content.matchAll(MD_LINK)) {
    if (m.index! > last) parts.push(content.slice(last, m.index));
    parts.push(
      <a key={m.index} href={m[2]} className="text-accent-ink underline underline-offset-2 hover:brightness-110">
        {m[1]}
      </a>,
    );
    last = m.index! + m[0].length;
  }
  if (parts.length === 0) return content;
  if (last < content.length) parts.push(content.slice(last));
  return parts;
}

interface ConvoSummary {
  id: string;
  title: string;
  datetime?: string;
  updated_at?: string;
  message_count: number;
}

const sessionKey = (agentId: string) => `chatSession:${agentId}`;

export default function Home() {
  const { messages, addMessage, appendToLastMessage, setIsStreaming, toolEvents, clearMessages, setMessages } = useSession();
  const [input, setInput] = useState('');
  const [waiting, setWaiting] = useState(false); // sent, before first token
  const [streaming, setStreaming] = useState(false); // actively streaming a reply
  const [planning, setPlanning] = useState<string | null>(null); // transient status (e.g. multi-step)
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Agent selection — chat + SSE must share one agent_id (they rendezvous on a
  // per-(user, agent) manager on the backend).
  const { data: agentsData, loading: agentsLoading } = useResource<{ id: string; name: string }[]>('/agents');
  const agents = useMemo(() => (agentsData || []).filter((a) => a.id), [agentsData]);
  const [agentId, setAgentId] = useState<string>(() => localStorage.getItem('selectedAgentId') || '');

  // Conversations — server-side sessions per agent (task runs excluded).
  const { data: convos, refetch: refetchConvos } = useResource<ConvoSummary[]>(
    agentId ? `/sessions/agents/${agentId}?exclude_tasks=true` : null,
  );
  const convosSorted = useMemo(
    () => [...(convos || [])].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')),
    [convos],
  );
  const convosRef = useRef(convosSorted);
  convosRef.current = convosSorted;

  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const activeRef = useRef<string | null>(null);
  activeRef.current = activeSessionId;
  const waitingRef = useRef(false);
  waitingRef.current = waiting;

  const busy = waiting || streaming;

  // Default to the first agent once the list loads (or if the saved one is gone).
  useEffect(() => {
    if (agents.length === 0) return;
    if (!agentId || !agents.some((a) => a.id === agentId)) {
      const first = agents[0].id;
      setAgentId(first);
      localStorage.setItem('selectedAgentId', first);
    }
  }, [agents, agentId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, waiting, planning]);

  const resetThreadState = useCallback(() => {
    clearMessages();
    setWaiting(false);
    setStreaming(false);
    setPlanning(null);
  }, [clearMessages]);

  const adoptSession = useCallback(
    (id: string | null) => {
      setActiveSessionId(id);
      if (agentId) {
        // '' is a deliberate-blank sentinel: a new empty thread survives reload
        // instead of falling back to the most recent conversation.
        localStorage.setItem(sessionKey(agentId), id ?? '');
      }
    },
    [agentId],
  );

  const loadConversation = useCallback(
    async (id: string) => {
      try {
        const res = await api.get(`/sessions/${id}/chat`);
        adoptSession(id);
        setMessages(toChatMessages(parseChatEntries(res.data)));
        setWaiting(false);
        setStreaming(false);
        setPlanning(null);
        return true;
      } catch {
        // Deleted/stale id — clear it and let the caller fall back.
        if (agentId) localStorage.removeItem(sessionKey(agentId));
        return false;
      }
    },
    [adoptSession, agentId, setMessages],
  );

  // Restore the last-active conversation when the agent resolves or changes.
  // Waits for the conversation list so the stored id can be validated against
  // it — a stale key could point at a deleted session or a task-run session
  // (which must never open as a chat thread).
  const restoredForAgent = useRef<string | null>(null);
  useEffect(() => {
    if (!agentId || convos === null) return;
    if (restoredForAgent.current === agentId) return;
    restoredForAgent.current = agentId;
    const stored = localStorage.getItem(sessionKey(agentId));
    if (stored === '') {
      // Sentinel: the user deliberately left a blank new thread.
      setActiveSessionId(null);
      resetThreadState();
      return;
    }
    if (stored && convosSorted.some((c) => c.id === stored)) {
      void loadConversation(stored);
      return;
    }
    if (stored) localStorage.removeItem(sessionKey(agentId));
    const recent = convosSorted[0];
    if (recent) void loadConversation(recent.id);
    else {
      setActiveSessionId(null);
      resetThreadState();
    }
  }, [agentId, convos, convosSorted, loadConversation, resetThreadState]);

  const handleSSEEvent = useCallback(
    (event: { type: string; content?: string; action?: string; session_id?: string }) => {
      const sid = event.session_id;
      // Drop only events that CARRY a mismatched id — untagged events (status,
      // transport errors) always belong to the active turn since switching is
      // disabled while streaming.
      if (sid && activeRef.current && sid !== activeRef.current) return;
      // A fresh thread's first reply carries the server-created session id.
      // Adopt it ONLY while we're awaiting our own reply: the SSE manager is
      // shared per (user, agent), so another tab/client's tagged events can
      // arrive here and must not hijack this thread.
      if (sid && !activeRef.current) {
        if (!waitingRef.current) return;
        setActiveSessionId(sid);
        if (agentId) localStorage.setItem(sessionKey(agentId), sid);
      }
      switch (event.type) {
        case 'generate':
          if (event.content) {
            appendToLastMessage(event.content);
            setWaiting(false);
            setStreaming(true);
            setPlanning(null);
          } else {
            // final empty chunk = stream complete
            setStreaming(false);
            setWaiting(false);
            refetchConvos({ silent: true });
          }
          break;
        case 'multistep_result':
          if (event.content) appendToLastMessage(event.content);
          setWaiting(false);
          setStreaming(false);
          setPlanning(null);
          refetchConvos({ silent: true });
          break;
        case 'status':
          // transient indicator (e.g. "Planning multi-step task…") — NOT a message
          if (event.content) setPlanning(event.content);
          break;
        case 'error':
          if (event.content) addMessage('assistant', `Error: ${event.content}`);
          if (event.content?.includes('no longer exists') && agentId) {
            localStorage.removeItem(sessionKey(agentId));
            setActiveSessionId(null);
          }
          setWaiting(false);
          setStreaming(false);
          setPlanning(null);
          break;
        default:
          break;
      }
    },
    [appendToLastMessage, addMessage, agentId, refetchConvos],
  );

  // Open the stream once we know which agent to use (or that there are none),
  // so a fresh visitor doesn't connect to the default agent then reconnect.
  const sseReady = !!agentId || (!agentsLoading && agents.length === 0);
  const { isConnected, error, reconnect } = useSSE({
    onMessage: handleSSEEvent,
    onError: useCallback((e: Error) => console.error('SSE error:', e), []),
    agentId: agentId || undefined,
    enabled: sseReady,
  });

  const send = useCallback(
    async (text: string) => {
      const msg = text.trim();
      if (!msg || waiting) return;
      addMessage('user', msg);
      setInput('');
      setWaiting(true);
      setStreaming(false);
      setPlanning(null);
      setIsStreaming(true);
      try {
        await api.post('/agents/chat', {
          message: msg,
          ...(agentId ? { agent_id: agentId } : {}),
          // No id on a fresh thread: the server creates the session and the
          // client adopts its id from the first tagged SSE event.
          ...(activeSessionId ? { session_id: activeSessionId } : {}),
        });
      } catch {
        addMessage('assistant', 'Unable to reach the agent. Check the connection and try again.');
        setWaiting(false);
      } finally {
        setIsStreaming(false);
      }
    },
    [waiting, addMessage, setIsStreaming, agentId, activeSessionId],
  );

  const switchConversation = (id: string) => {
    if (busy || id === activeSessionId) return;
    void loadConversation(id);
  };

  const newConversation = () => {
    if (busy) return;
    adoptSession(null);
    resetThreadState();
    setInput('');
    inputRef.current?.focus();
  };

  const deleteConversation = async (id: string) => {
    if (busy || !confirm('Delete this conversation?')) return;
    try {
      await api.delete(`/sessions/${id}`);
      refetchConvos({ silent: true });
      if (id === activeSessionId) newConversation();
    } catch {
      // list stays as-is; nothing destructive happened client-side
    }
  };

  const switchAgent = (id: string) => {
    if (id === agentId) return;
    setAgentId(id);
    localStorage.setItem('selectedAgentId', id);
    setActiveSessionId(null);
    resetThreadState();
    // restore effect picks up this agent's own last conversation
  };

  const empty = messages.length === 0;

  return (
    <div className="flex-1 flex h-screen min-w-0 bg-bg text-fg">
      {/* Conversations panel */}
      {agentId && (
        <aside className="hidden md:flex w-60 flex-none flex-col border-r border-line bg-panel">
          <div className="flex h-14 flex-none items-center justify-between border-b border-line px-4">
            <span className="font-mono text-[10px] tracking-[0.18em] text-fg-dim">CONVERSATIONS</span>
            <button
              onClick={newConversation}
              disabled={busy}
              className="rounded border border-line px-2 py-1 font-mono text-[11px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg disabled:pointer-events-none disabled:opacity-40"
            >
              + new
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {convosSorted.length === 0 ? (
              <p className="px-4 py-6 font-mono text-[11px] text-fg-dim">no conversations yet</p>
            ) : (
              convosSorted.map((c) => (
                <div
                  key={c.id}
                  onClick={() => switchConversation(c.id)}
                  className={cn(
                    'group flex cursor-pointer items-center gap-2 border-b border-line px-4 py-2.5 transition-colors',
                    c.id === activeSessionId ? 'bg-panel-2 border-l-2 border-l-accent' : 'hover:bg-panel-2',
                    busy && 'pointer-events-none opacity-60',
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px]">{c.title}</div>
                    <div className="mt-0.5 font-mono text-[10.5px] text-fg-dim">{fmtRelative(c.updated_at)}</div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      void deleteConversation(c.id);
                    }}
                    aria-label="Delete conversation"
                    className="hidden h-6 w-6 flex-none place-items-center rounded border border-line text-fg-dim group-hover:grid hover:border-danger hover:text-danger-ink"
                  >
                    ✕
                  </button>
                </div>
              ))
            )}
          </div>
        </aside>
      )}

      {/* Chat column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="flex h-14 flex-none items-center gap-4 border-b border-line px-6">
          <h1 className="text-[15px] font-semibold">Chat</h1>
          {agents.length > 0 && (
            <div className="relative">
              <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 font-mono text-[10px] text-fg-dim">/</span>
              <select
                value={agentId || agents[0]?.id || ''}
                onChange={(e) => switchAgent(e.target.value)}
                aria-label="Active agent"
                className="h-8 appearance-none rounded border border-line bg-panel-2 pl-5 pr-7 font-mono text-[12px] text-fg transition-colors hover:border-fg-dim focus:border-accent focus:outline-none"
              >
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
              <svg className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-fg-dim" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
            </div>
          )}
          <div className="ml-auto flex items-center gap-3">
            <span className="flex items-center gap-2 font-mono text-[11px] text-fg-dim">
              <span className={cn('h-1.5 w-1.5 rounded-full', isConnected ? 'bg-accent' : 'bg-danger')} />
              {isConnected ? 'connected' : (
                <button onClick={reconnect} className="underline underline-offset-2 hover:text-fg">reconnect</button>
              )}
            </span>
            <button
              onClick={newConversation}
              disabled={busy || (empty && !activeSessionId)}
              className="flex items-center gap-1.5 rounded border border-line px-3 py-1.5 font-mono text-[11px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg disabled:pointer-events-none disabled:opacity-40"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
              New session
            </button>
          </div>
        </header>

        {/* Stream */}
        <div className="flex-1 overflow-y-auto">
          {empty ? (
            <div className="mx-auto flex h-full max-w-2xl flex-col justify-center px-6">
              <p className="font-mono text-[12px] tracking-[0.04em] text-accent-ink">&gt;_ start a conversation</p>
              <h2 className="mt-3 text-2xl font-bold tracking-tight">What should we run?</h2>
              <p className="mt-2 max-w-md text-fg-dim">
                Ask a question, chat, or kick off a multi-step task — the agent plans and executes with its tools.
              </p>
              <div className="mt-6 flex flex-col gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="group flex items-center gap-3 rounded border border-line px-3.5 py-2.5 text-left text-sm text-fg-dim transition-colors hover:border-fg-dim hover:text-fg"
                  >
                    <span className="font-mono text-[11px] text-fg-dim group-hover:text-accent-ink">→</span>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div>
              {messages.map((m, i) => {
                const isUser = m.role === 'user';
                const isLast = i === messages.length - 1;
                return (
                  <div key={m.id} className="grid grid-cols-[68px_1fr] gap-4 border-b border-line px-6 py-4">
                    <div className={cn('pt-0.5 font-mono text-[11px] tracking-[0.06em]', isUser ? 'text-accent-ink' : 'text-fg-dim')}>
                      {isUser ? 'YOU' : 'AGENT'}
                    </div>
                    <div className="min-w-0">
                      <div className="whitespace-pre-wrap break-words leading-relaxed">
                        {isUser ? m.content : renderWithLinks(m.content)}
                        {!isUser && isLast && streaming && <span className="caret" />}
                      </div>
                      {m.timestamp && (
                        <div className="mt-2 font-mono text-[10.5px] text-fg-dim">
                          <span className="opacity-70">at</span> {fmtTime(m.timestamp)}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}

              {waiting && (
                <div className="grid grid-cols-[68px_1fr] gap-4 px-6 py-4">
                  <div className="pt-0.5 font-mono text-[11px] tracking-[0.06em] text-fg-dim">AGENT</div>
                  <div className="flex items-center gap-2.5 font-mono text-[12px] text-fg-dim">
                    <span className="think-bars"><i /><i /><i /><i /></span>
                    {planning || 'Thinking…'}
                  </div>
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="mx-6 my-4 flex items-center justify-between gap-3 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">
              <span>connection error — the stream stopped after several retries.</span>
              <button onClick={reconnect} className="flex-none rounded border border-line px-2 py-0.5 transition-colors hover:border-fg-dim hover:text-fg">
                ↻ retry
              </button>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {/* Composer */}
        <div className="flex-none border-t border-line px-6 py-4">
          <div className="mx-auto max-w-3xl">
            <div className="flex items-end gap-2 rounded-md border border-line bg-panel px-3 py-2.5 focus-within:border-accent focus-within:shadow-ring transition-colors">
              <textarea
                ref={inputRef}
                rows={1}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    send(input);
                  }
                }}
                placeholder="Message the agent…"
                className="max-h-40 flex-1 resize-none bg-transparent text-sm text-fg outline-none placeholder:text-fg-dim"
              />
              <button
                onClick={() => send(input)}
                disabled={!input.trim() || waiting}
                aria-label="Send"
                className="grid h-8 w-8 flex-none place-items-center rounded bg-accent text-accent-foreground transition disabled:opacity-40 disabled:cursor-not-allowed hover:brightness-105"
              >
                {waiting ? (
                  <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" className="opacity-25" /><path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" /></svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 12l16-8-6 16-3-6-7-2z" /></svg>
                )}
              </button>
            </div>
            <div className="mt-2 flex items-center gap-4 font-mono text-[10.5px] text-fg-dim">
              <span><kbd className="rounded border border-line px-1">⏎</kbd> send</span>
              <span><kbd className="rounded border border-line px-1">⇧⏎</kbd> newline</span>
              {toolEvents.length > 0 && (
                <span className="ml-auto flex items-center gap-1.5 text-accent-ink">
                  <span className="think-bars"><i /><i /><i /></span>
                  {toolEvents[toolEvents.length - 1].toolName}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
