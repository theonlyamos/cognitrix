import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useEventStream } from '@/hooks/useEventStream';
import { ChatMessageRow } from '@/components/ChatMessageRow';
import { QuestionCard, type QuestionRequest } from '@/components/QuestionCard';
import { LoadingState, ErrorState } from '@/components/list-ui';
import type { ChatMessage, ToolArtifact } from '@/context/SessionContext';
import { parseChatEntries, toChatMessages, fmtRelative, type BackendChatEntry } from '@/lib/transcript';
import { cn } from '@/lib/utils';

interface Member { id: string; name: string; handle: string; lead: boolean; provider?: string | null; model?: string | null }
interface ChatSummary { id: string; title: string; updated_at?: string | null; running: boolean }
interface RunSummary { run_id: string; history_len?: number; prompt?: string }
interface ChatsResponse { team: { id: string; name: string; leader_id?: string | null }; members: Member[]; chats: ChatSummary[] }
interface ChatDetail extends ChatSummary { messages: BackendChatEntry[]; run?: RunSummary | null }

interface ApprovalRequest {
  request_id: string;
  tool_name: string;
  params?: Record<string, unknown>;
  risk_level: string;
  details?: string;
}

interface RunEvent {
  type: string;
  session_id?: string;
  content?: string;
  name?: string;
  prompt?: string;
  run_id?: string;
  started_at?: string;
  tool_name?: string;
  tool_call_id?: string;
  status?: string;
  params?: string;
  result?: string;
  artifacts?: ToolArtifact[];
  [key: string]: unknown;
}

const EVERYONE: Member = { id: '*', name: 'Everyone', handle: 'all', lead: false };

let nextId = 0;
const newId = () => `gc-${Date.now()}-${nextId++}`;

function toMessages(entries: BackendChatEntry[]): ChatMessage[] {
  return toChatMessages(parseChatEntries(entries), { speakers: true });
}

/** The @word being typed right before the cursor, if any. */
function mentionQuery(text: string, cursor: number): { start: number; query: string } | null {
  const before = text.slice(0, cursor);
  const match = /(^|\s)@([\w-]*)$/.exec(before);
  if (!match) return null;
  return { start: before.length - match[2].length - 1, query: match[2].toLowerCase() };
}

export default function TeamChat() {
  const { teamId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const chatId = params.get('chat');

  const [info, setInfo] = useState<ChatsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [speaker, setSpeaker] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [question, setQuestion] = useState<QuestionRequest | null>(null);
  const [follow, setFollow] = useState<{ sid: string; runId: string } | null>(null);
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);

  const baseRef = useRef<ChatMessage[]>([]);
  const speakerRef = useRef<string | null>(null);
  const chatIdRef = useRef(chatId);
  chatIdRef.current = chatId;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadInfo = useCallback(async () => {
    try {
      const res = await api.get<ChatsResponse>(`/teams/${teamId}/chats`);
      setInfo(res.data);
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err, 'Could not load this team.'));
    }
  }, [teamId]);

  useEffect(() => { void loadInfo(); }, [loadInfo]);

  const loadChat = useCallback(async (id: string | null) => {
    setApprovals([]);
    setQuestion(null);
    setStatus(null);
    setSendError(null);
    if (!id) {
      setMessages([]);
      setRunning(false);
      setFollow(null);
      return;
    }
    try {
      const res = await api.get<ChatDetail>(`/teams/${teamId}/chats/${id}`);
      if (chatIdRef.current !== id) return;
      const saved = res.data.messages || [];
      if (res.data.run) {
        // The team is still answering: show the history before this run,
        // then replay the run on top of it.
        baseRef.current = toMessages(saved.slice(0, res.data.run.history_len ?? saved.length));
        setMessages(baseRef.current);
        setRunning(true);
        setFollow({ sid: id, runId: res.data.run.run_id });
      } else {
        setMessages(toMessages(saved));
        setRunning(false);
        setFollow(null);
      }
    } catch (err) {
      setSendError(errorMessage(err, 'Could not open this chat.'));
    }
  }, [teamId]);

  useEffect(() => { void loadChat(chatId); }, [chatId, loadChat]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, approvals, question]);

  const finish = useCallback(() => {
    setRunning(false);
    setSpeaker(null);
    speakerRef.current = null;
    setFollow(null);
    setApprovals([]);
    setQuestion(null);
    void loadInfo();
    if (chatIdRef.current) void loadChat(chatIdRef.current);
  }, [loadChat, loadInfo]);

  const onEvent = useCallback((event: RunEvent) => {
    switch (event.type) {
      case 'run_attached': {
        const base = [...baseRef.current];
        const prompt = (event.prompt || '').trim();
        if (prompt) base.push({ id: `run-${event.run_id}`, role: 'user', content: prompt, timestamp: event.started_at });
        setMessages(base);
        setRunning(true);
        break;
      }
      case 'group_speaker': {
        const name = event.name || 'Agent';
        speakerRef.current = name;
        setSpeaker(name);
        setStatus(null);
        setMessages((prev) => [...prev, { id: newId(), role: 'assistant', content: '', speaker: name }]);
        break;
      }
      case 'generate':
        if (!event.content) break;
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === 'assistant') {
            return [...prev.slice(0, -1), { ...last, content: last.content + event.content }];
          }
          return [...prev, { id: newId(), role: 'assistant', content: event.content || '', speaker: speakerRef.current || undefined }];
        });
        break;
      case 'tool':
        if (!event.tool_name) break;
        setMessages((prev) => {
          if (event.status === 'started') {
            const last = prev[prev.length - 1];
            const chip = { id: event.tool_call_id, name: event.tool_name!, status: 'running' as const, params: event.params };
            if (last?.role === 'tool') return [...prev.slice(0, -1), { ...last, tools: [...(last.tools || []), chip] }];
            return [...prev, { id: newId(), role: 'tool', content: '', tools: [chip], speaker: speakerRef.current || undefined }];
          }
          const done = event.status === 'error' ? 'error' as const : 'done' as const;
          for (let i = prev.length - 1; i >= 0; i -= 1) {
            const row = prev[i];
            if (row.role !== 'tool' || !row.tools) continue;
            const index = row.tools.findIndex((t) => (event.tool_call_id ? t.id === event.tool_call_id : t.name === event.tool_name) && t.status === 'running');
            if (index < 0) continue;
            const tools = row.tools.map((t, j) => (j === index ? { ...t, status: done, result: event.result, artifacts: event.artifacts } : t));
            return [...prev.slice(0, i), { ...row, tools }, ...prev.slice(i + 1)];
          }
          return prev;
        });
        break;
      case 'status':
        if (event.content) setStatus(event.content);
        break;
      case 'approval_request': {
        const req = event as unknown as ApprovalRequest;
        if (req.request_id) setApprovals((prev) => (prev.some((a) => a.request_id === req.request_id) ? prev : [...prev, req]));
        break;
      }
      case 'question_request': {
        const req = event as unknown as QuestionRequest;
        if (req.request_id && req.prompt && Array.isArray(req.options)) setQuestion(req);
        break;
      }
      case 'error':
        if (event.content) setMessages((prev) => [...prev, { id: newId(), role: 'assistant', content: `Error: ${event.content}`, speaker: speakerRef.current || undefined }]);
        finish();
        break;
      case 'turn_complete':
      case 'turn_stopped':
        finish();
        break;
      default:
        break;
    }
  }, [finish]);

  useEventStream<RunEvent>({
    path: follow ? `/agents/runs/${encodeURIComponent(follow.sid)}/events?run=${encodeURIComponent(follow.runId)}` : null,
    enabled: !!follow,
    maxRetries: 3,
    onEvent: ({ data }) => onEvent(data),
    onError: useCallback(() => finish(), [finish]),
  });

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || running) return;
    setSendError(null);
    setStatus(null);
    setRunning(true);
    baseRef.current = messages;
    setMessages([...messages, { id: newId(), role: 'user', content: text }]);
    setInput('');
    setMention(null);
    try {
      const res = await api.post<{ session_id: string; run_id: string }>(`/teams/${teamId}/chats`, {
        message: text,
        ...(chatId ? { session_id: chatId } : {}),
      });
      const sid = res.data.session_id;
      if (sid !== chatId) {
        chatIdRef.current = sid;
        setParams({ chat: sid }, { replace: !chatId });
      }
      setFollow({ sid, runId: res.data.run_id });
      void loadInfo();
    } catch (err) {
      setRunning(false);
      setMessages(baseRef.current);
      setInput(text);
      setSendError(errorMessage(err, 'Could not send the message.'));
    }
  }, [chatId, input, loadInfo, messages, running, setParams, teamId]);

  const stop = useCallback(async () => {
    if (!chatId) return;
    try {
      await api.post(`/teams/${teamId}/chats/${chatId}/stop`);
    } catch (err) {
      setSendError(errorMessage(err, 'Could not stop the team.'));
    }
  }, [chatId, teamId]);

  const removeChat = useCallback(async () => {
    if (!chatId || running || !confirm('Delete this group chat?')) return;
    try {
      await api.delete(`/teams/${teamId}/chats/${chatId}`);
      setParams({});
      void loadInfo();
    } catch (err) {
      setSendError(errorMessage(err, 'Could not delete the chat.'));
    }
  }, [chatId, loadInfo, running, setParams, teamId]);

  const respondApproval = useCallback(async (requestId: string, approved: boolean, remember = false) => {
    setApprovals((prev) => prev.filter((a) => a.request_id !== requestId));
    try {
      await api.post('/agents/approval', { request_id: requestId, approved, remember });
    } catch {
      /* the turn times out server-side if this never lands */
    }
  }, []);

  const questionAction = useCallback(async (
    current: QuestionRequest,
    action: 'answer' | 'cancel' | 'stop_timer',
    answer?: { option_id: string } | { text: string },
  ) => {
    await api.post('/agents/question', { request_id: current.request_id, action, ...answer });
    if (action === 'stop_timer') {
      setQuestion((q) => (q?.request_id === current.request_id ? { ...q, auto_submit_seconds: null, auto_submit_at: null } : q));
    } else {
      setQuestion((q) => (q?.request_id === current.request_id ? null : q));
    }
  }, []);

  const members = useMemo(() => info?.members ?? [], [info]);
  const options = useMemo(() => {
    if (!mention) return [];
    const all = members.length > 1 ? [...members, EVERYONE] : members;
    return all.filter((m) => m.handle.toLowerCase().startsWith(mention.query) || m.name.toLowerCase().startsWith(mention.query));
  }, [members, mention]);
  const menuOpen = options.length > 0;

  const updateMention = (value: string, cursor: number) => {
    setMention(mentionQuery(value, cursor));
    setMentionIndex(0);
  };

  const pickMention = (member: Member) => {
    if (!mention) return;
    const cursor = inputRef.current?.selectionStart ?? input.length;
    const next = `${input.slice(0, mention.start)}@${member.handle} ${input.slice(cursor)}`;
    setInput(next);
    setMention(null);
    const caret = mention.start + member.handle.length + 2;
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(caret, caret);
    });
  };

  if (loadError && !info) {
    return <div className="flex h-full min-w-0 flex-1 flex-col bg-bg"><ErrorState message={loadError} onRetry={loadInfo} /></div>;
  }
  if (!info) return <div className="flex h-full min-w-0 flex-1 flex-col bg-bg"><LoadingState label="loading team chat…" /></div>;

  const lead = members.find((m) => m.lead);
  const placeholder = members.length ? `Message the team… (@${members[0].handle} to ask one agent)` : 'Add agents to this team first';

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <header className="app-page-header flex min-h-14 flex-none flex-row flex-nowrap items-center gap-2 border-b border-line py-2 pl-16 pr-4 md:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden">
          <Link to={`/teams/${teamId}`} aria-label="Back to team" className="grid h-11 w-11 flex-none place-items-center rounded border border-line text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:h-8 md:w-8">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
          </Link>
          <div className="min-w-0">
            <p className="sr-only font-mono text-[10px] tracking-[0.18em] text-accent-ink md:not-sr-only">GROUP CHAT</p>
            <h1 className="truncate text-lg font-semibold tracking-tight">{info.team.name}</h1>
          </div>
        </div>
        <div className="flex flex-none items-center gap-2">
          <label className="sr-only" htmlFor="group-chat-picker">Group chats</label>
          <select
            id="group-chat-picker"
            value={chatId ?? ''}
            onChange={(e) => setParams(e.target.value ? { chat: e.target.value } : {})}
            className="h-11 w-28 truncate rounded border border-line bg-panel px-2 font-mono text-[11px] text-fg md:h-8 md:w-auto md:max-w-[16rem]"
          >
            <option value="">New chat</option>
            {info.chats.map((c) => (
              <option key={c.id} value={c.id}>{c.running ? '● ' : ''}{c.title}{c.updated_at ? ` · ${fmtRelative(c.updated_at)}` : ''}</option>
            ))}
          </select>
          {chatId && (
            <button
              type="button"
              onClick={() => setParams({})}
              className="hidden h-8 rounded border border-line px-2.5 font-mono text-[11px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:block"
            >
              + new
            </button>
          )}
          {chatId && !running && (
            <button
              type="button"
              onClick={removeChat}
              aria-label="Delete this chat"
              className="grid h-11 w-11 place-items-center rounded text-fg-dim transition-colors hover:text-danger-ink md:h-8 md:w-8"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /></svg>
            </button>
          )}
        </div>
      </header>

      <div className="flex flex-none flex-wrap items-center gap-1.5 border-b border-line px-4 py-2 md:px-6" aria-label="Team members">
        {members.map((m) => (
          <button
            key={m.id}
            type="button"
            title={`${m.provider || '—'} · ${m.model || '—'}`}
            onClick={() => { setInput((v) => `${v}${v && !v.endsWith(' ') ? ' ' : ''}@${m.handle} `); inputRef.current?.focus(); }}
            className={cn(
              'inline-flex min-h-9 items-center gap-1.5 rounded border px-2 py-0.5 font-mono text-[11px] transition-colors md:min-h-0',
              speaker === m.name ? 'border-accent text-accent-ink' : 'border-line text-fg-dim hover:border-fg-dim hover:text-fg',
            )}
          >
            <span className="grid h-4 w-4 place-items-center rounded-sm bg-panel-2 text-[9px] font-bold text-accent-ink">{m.name[0]?.toUpperCase()}</span>
            @{m.handle}
            {m.lead && <span className="text-[9px] text-accent-ink">LEAD</span>}
            {speaker === m.name && <span className="think-bars" aria-label="replying"><i /><i /><i /></span>}
          </button>
        ))}
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="mx-auto max-w-xl px-6 py-14">
            <h2 className="text-xl font-semibold tracking-tight">Talk to the whole team</h2>
            <ul className="mt-3 space-y-1.5 text-sm text-fg-dim">
              <li><span className="font-mono text-accent-ink">@name</span> asks that agent. Mention several and they answer in that order.</li>
              <li><span className="font-mono text-accent-ink">@all</span> asks everyone.</li>
              <li>No mention: the agent who spoke last answers{lead ? `, or @${lead.handle} (the lead) to start` : ''}.</li>
              <li>Agents can hand work to each other by @mentioning a teammate.</li>
            </ul>
          </div>
        ) : (
          <div>
            {messages.map((message, index) => (
              <ChatMessageRow
                key={message.id}
                message={message}
                isLast={index === messages.length - 1}
                streaming={running && index === messages.length - 1 && message.role === 'assistant'}
              />
            ))}
            {running && (
              <div role="status" className="grid grid-cols-1 gap-2 px-4 py-4 sm:grid-cols-[68px_1fr] sm:gap-4 sm:px-6">
                <div className="truncate pt-0.5 font-mono text-[11px] tracking-[0.06em] text-fg-dim">{speaker ? speaker.toUpperCase() : 'TEAM'}</div>
                <div className="flex items-center gap-2.5 font-mono text-[12px] text-fg-dim">
                  <span className="think-bars"><i /><i /><i /><i /></span>
                  {status || (speaker ? `${speaker} is working…` : 'Picking who answers…')}
                </div>
              </div>
            )}
            {!running && status && (
              <p className="px-6 py-3 font-mono text-[11px] text-fg-dim">{status}</p>
            )}
          </div>
        )}

        {question && (
          <QuestionCard
            question={question}
            onAnswer={(answer) => questionAction(question, 'answer', answer)}
            onCancel={() => questionAction(question, 'cancel')}
            onStopTimer={() => questionAction(question, 'stop_timer')}
          />
        )}

        {approvals.map((a) => (
          <div key={a.request_id} role="alert" className="animate-rise mx-6 my-4 border-l-2 border-danger bg-danger/5 px-3 py-3 font-mono text-[12px]">
            <div className="flex items-center gap-2">
              <span className="text-danger-ink">⚠ approval required{speaker ? ` · ${speaker}` : ''}</span>
              <span className="rounded border border-line px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-fg-dim">{a.risk_level} risk</span>
            </div>
            <div className="mt-2 text-fg">
              run <span className="text-accent-ink">{a.tool_name}</span>
              {a.details && <span className="text-fg-dim"> — {a.details}</span>}
            </div>
            {a.params && Object.keys(a.params).length > 0 && (
              <pre className="mt-2 max-h-32 overflow-auto rounded border border-line bg-panel-2 p-2 text-[11px] text-fg-dim">{JSON.stringify(a.params, null, 2)}</pre>
            )}
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <button onClick={() => respondApproval(a.request_id, true)} className="min-h-11 rounded border border-line px-2.5 py-1 text-[11px] text-fg-dim transition-colors hover:border-accent hover:text-accent-ink md:min-h-0">approve</button>
              <button onClick={() => respondApproval(a.request_id, true, true)} className="min-h-11 rounded border border-line px-2.5 py-1 text-[11px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:min-h-0">approve for session</button>
              <button onClick={() => respondApproval(a.request_id, false)} className="min-h-11 rounded border border-line px-2.5 py-1 text-[11px] text-fg-dim transition-colors hover:border-danger hover:text-danger-ink md:min-h-0">deny</button>
            </div>
          </div>
        ))}
      </div>

      <div className="flex-none border-t border-line px-4 py-4 md:px-6">
        <div className="relative mx-auto max-w-3xl">
          {menuOpen && (
            <div id="mention-menu" role="listbox" aria-label="Mention a teammate" className="animate-rise absolute bottom-full left-0 right-0 z-20 mb-2 overflow-hidden rounded-md border border-line bg-panel shadow-lg">
              <div className="border-b border-line px-3 py-1.5 font-mono text-[10px] tracking-[0.18em] text-fg-dim">MENTION</div>
              {options.map((m, i) => (
                <button
                  key={m.id}
                  id={`mention-opt-${i}`}
                  role="option"
                  aria-selected={i === mentionIndex}
                  onMouseEnter={() => setMentionIndex(i)}
                  onMouseDown={(e) => { e.preventDefault(); pickMention(m); }}
                  className={cn('flex w-full items-baseline gap-2 border-l-2 px-3 py-2 text-left transition-colors', i === mentionIndex ? 'border-l-accent bg-panel-2' : 'border-l-transparent hover:bg-panel-2')}
                >
                  <span className="font-mono text-[12px] text-accent-ink">@{m.handle}</span>
                  <span className="ml-auto truncate pl-3 text-[11px] text-fg-dim">{m.id === '*' ? 'every agent answers' : m.lead ? 'team lead' : m.model || ''}</span>
                </button>
              ))}
            </div>
          )}
          <div className="relative rounded-md border border-line bg-panel transition-colors focus-within:border-accent">
            <textarea
              ref={inputRef}
              rows={1}
              value={input}
              disabled={members.length === 0}
              onChange={(e) => { setInput(e.target.value); updateMention(e.target.value, e.target.selectionStart ?? e.target.value.length); }}
              onSelect={(e) => updateMention(e.currentTarget.value, e.currentTarget.selectionStart ?? 0)}
              onBlur={() => setMention(null)}
              onKeyDown={(e) => {
                if (menuOpen) {
                  if (e.key === 'ArrowDown') { e.preventDefault(); setMentionIndex((i) => Math.min(i + 1, options.length - 1)); return; }
                  if (e.key === 'ArrowUp') { e.preventDefault(); setMentionIndex((i) => Math.max(i - 1, 0)); return; }
                  if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickMention(options[mentionIndex]); return; }
                  if (e.key === 'Escape') { e.preventDefault(); setMention(null); return; }
                }
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); }
              }}
              role="combobox"
              aria-label="Message the team"
              aria-expanded={menuOpen}
              aria-controls="mention-menu"
              aria-activedescendant={menuOpen ? `mention-opt-${mentionIndex}` : undefined}
              placeholder={placeholder}
              className="block min-h-11 max-h-40 w-full resize-none bg-transparent px-3 py-2.5 pr-14 text-sm text-fg placeholder:text-fg-dim md:min-h-0"
            />
            <button
              onClick={() => { if (running) void stop(); else void send(); }}
              disabled={!running && !input.trim()}
              aria-label={running ? 'Stop the team' : 'Send'}
              className={cn(
                'absolute bottom-1 right-1.5 grid h-11 w-11 place-items-center rounded transition disabled:cursor-not-allowed disabled:opacity-60 hover:brightness-105 md:h-8 md:w-8',
                running ? 'bg-danger text-bg' : 'bg-accent text-accent-foreground',
              )}
            >
              {running ? (
                <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1" /></svg>
              ) : (
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 12l16-8-6 16-3-6-7-2z" /></svg>
              )}
            </button>
          </div>
          {sendError && <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-danger-ink">{sendError}</div>}
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[10.5px] text-fg-dim">
            <span>@name picks who answers · @all asks everyone</span>
            <span className="ml-auto hidden sm:inline"><kbd className="rounded border border-line px-1">⏎</kbd> send</span>
            <span className="hidden sm:inline"><kbd className="rounded border border-line px-1">⇧⏎</kbd> newline</span>
          </div>
        </div>
      </div>
    </div>
  );
}
