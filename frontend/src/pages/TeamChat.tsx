import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useEventStream } from '@/hooks/useEventStream';
import { ChatMessageRow } from '@/components/ChatMessageRow';
import { QuestionCard, type QuestionRequest } from '@/components/QuestionCard';
import { LoadingState, ErrorState } from '@/components/list-ui';
import { ChatHorizon } from '@/components/ThemeArtwork';
import { AgentBadge } from '@/components/StartChat';
import { PanelButton, PanelCard } from '@/components/cards/PanelCard';
import { MicButton, VoiceBar, VoiceNotice, useVoiceInput } from '@/components/VoiceInput';
import { insertTranscript } from '@/lib/voice';
import { composerBtn, composerIconBtn } from '@/components/composerStyles';
import { ACCEPT_ATTR, checkFiles, type Attachment } from '@/lib/chatAttachments';
import type { ChatMessage, ToolArtifact } from '@/context/SessionContext';
import { parseChatEntries, toChatMessages, type BackendChatEntry } from '@/lib/transcript';
import { cn } from '@/lib/utils';
import { getSendKey, isSendKey } from '@/lib/preferences';
import { SendKeyHint } from '@/components/SendKeyHint';
import { useApprovalSeen } from '@/hooks/useApprovalSeen';
import { setViewingChat } from '@/lib/chatPresence';
import { CHATS_EDITED, chatsStale, setChatNav, type ChatsEditedDetail } from '@/lib/chatNav';
import { tr } from '@/lib/i18n';

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
  /** A full-shell command: the card offers "allow all commands in this chat". */
  full_shell?: boolean;
  /** A background action in an app window: the card offers to stop asking about that app. */
  app?: { key: string; label: string };
  /** Takes over the screen: asked every time, so nothing to remember. */
  always_ask?: boolean;
  always?: { kind: 'tool' | 'command'; label: string; computer?: string };
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

const humanizeToolName = (name: string) => name.replace(/_/g, ' ');

const EVERYONE: Member = { id: '*', name: 'Everyone', handle: 'all', lead: false };

let nextId = 0;
const newId = () => `gc-${Date.now()}-${nextId++}`;

const UPLOAD_NOTE = '[User uploaded files, readable with your file tools:]';

function toMessages(entries: BackendChatEntry[]): ChatMessage[] {
  // The note that tells agents where attached files are is shown to the
  // person as file names on the message it came with, as it was when sent.
  const shown: BackendChatEntry[] = [];
  for (const entry of entries) {
    const content = typeof entry.content === 'string' ? entry.content : '';
    if (String(entry.role).toLowerCase() !== 'user' || !content.startsWith(UPLOAD_NOTE)) { shown.push(entry); continue; }
    const given = (entry as { attachment_names?: unknown }).attachment_names;
    const names = Array.isArray(given) && given.length
      ? given.map(String)
      : content.slice(UPLOAD_NOTE.length).split('\n').map((line) => line.trim().split('/').pop() || '').filter(Boolean);
    const label = `📎 ${names.join(', ')}`;
    let at = shown.length - 1;
    while (at >= 0 && String(shown[at].role).toLowerCase() === 'user' && shown[at].type === 'image') at -= 1;
    const before = at >= 0 ? shown[at] : undefined;
    if (before && String(before.role).toLowerCase() === 'user' && (before.type ?? 'text') === 'text' && typeof before.content === 'string') {
      shown[at] = { ...before, content: `${before.content}${before.content ? '\n' : ''}${label}` };
    } else {
      shown.push({ ...entry, content: label });
    }
  }
  return toChatMessages(parseChatEntries(shown), { speakers: true });
}

/** The @word being typed right before the cursor, if any. */
function mentionQuery(text: string, cursor: number): { start: number; query: string } | null {
  const before = text.slice(0, cursor);
  const match = /(^|\s)@([\w-]*)$/.exec(before);
  if (!match) return null;
  return { start: before.length - match[2].length - 1, query: match[2].toLowerCase() };
}

/**
 * A chat with a whole team. It lives on the main chat screen
 * (/home?team=<id>&chat=<id>) and looks like a chat with one agent: the same
 * header, message list and message box, with @mentions picking who answers.
 */
export default function TeamChat({ teamId: teamProp }: { teamId?: string } = {}) {
  const routed = useParams().teamId;
  const teamId = teamProp ?? routed ?? '';
  const navigate = useNavigate();
  const [params, setSearch] = useSearchParams();
  const chatId = params.get('chat');
  // Only the chat changes here; which team is open stays in the address.
  const setParams = useCallback((next: { chat?: string }, opts?: { replace?: boolean }) => {
    setSearch((prev) => {
      const copy = new URLSearchParams(prev);
      if (next.chat) copy.set('chat', next.chat);
      else copy.delete('chat');
      return copy;
    }, opts);
  }, [setSearch]);

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
  useApprovalSeen(approvals);
  useEffect(() => {
    setViewingChat(chatId);
    setChatNav({ activeChatId: chatId });
    return () => {
      setViewingChat(null);
      setChatNav({ activeChatId: null });
    };
  }, [chatId]);
  const [follow, setFollow] = useState<{ sid: string; runId: string } | null>(null);
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);

  const baseRef = useRef<ChatMessage[]>([]);
  const speakerRef = useRef<string | null>(null);
  const chatIdRef = useRef(chatId);
  chatIdRef.current = chatId;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Files waiting to go with the next message (pictures are shown to the agents, documents can be read by them).
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;
  const addFiles = useCallback((list: FileList | File[]) => {
    const incoming = Array.from(list);
    if (!incoming.length) return;
    const { added, error } = checkFiles(incoming, attachmentsRef.current);
    setUploadError(error);
    if (added.length) setAttachments((prev) => [...prev, ...added]);
  }, []);
  const dropPreview = (a: Attachment) => { if (a.previewUrl && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(a.previewUrl); };
  const removeAttachment = (id: string) => {
    setUploadError(null);
    setAttachments((prev) => prev.filter((a) => { if (a.id === id) dropPreview(a); return a.id !== id; }));
  };

  // Voice input: the transcript goes in at the cursor, ready to edit.
  const voice = useVoiceInput({
    onText: (text) => {
      const el = inputRef.current;
      setInput((current) => {
        const start = el ? Math.min(el.selectionStart ?? current.length, current.length) : current.length;
        const end = el ? Math.min(el.selectionEnd ?? start, current.length) : start;
        const next = insertTranscript(current, text, start, end);
        requestAnimationFrame(() => {
          const box = inputRef.current;
          if (!box) return;
          box.focus();
          box.setSelectionRange(next.cursor, next.cursor);
        });
        return next.value;
      });
    },
  });
  // The message box grows with what is typed.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const loadInfo = useCallback(async () => {
    try {
      const res = await api.get<ChatsResponse>(`/teams/${teamId}/chats`);
      setInfo(res.data);
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err, tr('ui.teamChat.couldNotLoadThisTeam')));
    }
  }, [teamId]);

  useEffect(() => { void loadInfo(); }, [loadInfo]);
  // A turn ended or a chat was made or deleted here: the app sidebar re-reads.
  const refresh = useCallback(() => {
    void loadInfo();
    chatsStale();
  }, [loadInfo]);

  const loadChat = useCallback(async (id: string | null, opts: { keepStatus?: boolean } = {}) => {
    setApprovals([]);
    setQuestion(null);
    // A refresh after a turn keeps its last notice (e.g. "Paused after 6 replies").
    if (!opts.keepStatus) setStatus(null);
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
      setSendError(errorMessage(err, tr('ui.teamChat.couldNotOpenThisChat')));
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
    refresh();
    if (chatIdRef.current) void loadChat(chatIdRef.current, { keepStatus: true });
  }, [loadChat, refresh]);

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
      case 'attachments_ingested': {
        const pictures = (event.artifacts || [])
          .filter((artifact) => artifact && typeof artifact.id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(artifact.id)
            && typeof artifact.mime_type === 'string' && artifact.mime_type.startsWith('image/'))
          .map((artifact) => ({ ...artifact, origin: 'uploaded' as const }));
        if (!pictures.length) break;
        setMessages((prev) => {
          for (let i = prev.length - 1; i >= 0; i -= 1) {
            if (prev[i].role !== 'user') continue;
            const have = new Set((prev[i].artifacts || []).map((x) => x.id));
            const fresh = pictures.filter((x) => !have.has(x.id));
            if (!fresh.length) return prev;
            return [...prev.slice(0, i), { ...prev[i], artifacts: [...(prev[i].artifacts || []), ...fresh] }, ...prev.slice(i + 1)];
          }
          return prev;
        });
        break;
      }
      case 'status':
        if (event.content) setStatus(event.content);
        break;
      case 'approval_request': {
        const req = event as unknown as ApprovalRequest;
        if (req.request_id) setApprovals((prev) => (prev.some((a) => a.request_id === req.request_id) ? prev : [...prev, req]));
        break;
      }
      case 'approval_resolved': {
        const rid = (event as { request_id?: string }).request_id;
        if (rid) setApprovals((prev) => prev.filter((a) => a.request_id !== rid));
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
    const files = attachments;
    if ((!text && files.length === 0) || running) return;
    setSendError(null);
    setUploadError(null);
    setStatus(null);
    setRunning(true);
    baseRef.current = messages;
    // Keep a record of what was attached in the message on screen.
    const shown = files.length ? `${text}${text ? '\n' : ''}📎 ${files.map((a) => a.name).join(', ')}` : text;
    setMessages([...messages, { id: newId(), role: 'user', content: shown }]);
    setInput('');
    setAttachments([]);
    setMention(null);
    try {
      const payload = { message: text, ...(chatId ? { session_id: chatId } : {}) };
      let res;
      if (files.length) {
        const form = new FormData();
        form.append('payload', new Blob([JSON.stringify(payload)], { type: 'application/json' }));
        for (const a of files) form.append('files', a.file, a.name);
        res = await api.post<{ session_id: string; run_id: string }>(`/teams/${teamId}/chats`, form);
        files.forEach(dropPreview);
      } else {
        res = await api.post<{ session_id: string; run_id: string }>(`/teams/${teamId}/chats`, payload);
      }
      const sid = res.data.session_id;
      if (sid !== chatId) {
        chatIdRef.current = sid;
        setParams({ chat: sid }, { replace: !chatId });
      }
      setFollow({ sid, runId: res.data.run_id });
      refresh();
    } catch (err) {
      setRunning(false);
      setMessages(baseRef.current);
      setInput(text);
      setAttachments(files);
      setSendError(errorMessage(err, tr('ui.teamChat.couldNotSendTheMessage')));
    }
  }, [attachments, chatId, input, refresh, messages, running, setParams, teamId]);

  const stop = useCallback(async () => {
    if (!chatId) return;
    try {
      await api.post(`/teams/${teamId}/chats/${chatId}/stop`);
    } catch (err) {
      setSendError(errorMessage(err, tr('ui.teamChat.couldNotStopTheTeam')));
    }
  }, [chatId, teamId]);

  // The app sidebar renamed, pinned, archived or deleted a chat: re-read, and
  // leave this chat if it was the one deleted.
  useEffect(() => {
    const onEdited = (event: Event) => {
      const deleted = (event as CustomEvent<ChatsEditedDetail>).detail?.deletedId;
      if (deleted && deleted === chatIdRef.current) setParams({});
      void loadInfo();
    };
    window.addEventListener(CHATS_EDITED, onEdited);
    return () => window.removeEventListener(CHATS_EDITED, onEdited);
  }, [loadInfo, setParams]);

  const respondApproval = useCallback(async (requestId: string, approved: boolean, remember = false, rememberShell = false, rememberApp?: 'chat' | 'always', always = false) => {
    setApprovals((prev) => prev.filter((a) => a.request_id !== requestId));
    try {
      await api.post('/agents/approval', { request_id: requestId, approved, remember, ...(rememberShell ? { remember_shell: true } : {}), ...(rememberApp ? { remember_app: rememberApp } : {}), ...(always ? { always: true } : {}) });
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
  if (!info) return <div className="flex h-full min-w-0 flex-1 flex-col bg-bg"><LoadingState label={tr('ui.teamChat.loadingTeamChat')} /></div>;

  const lead = members.find((m) => m.lead);
  const empty = messages.length === 0 && !chatId;
  const activeChat = info.chats.find((c) => c.id === chatId);
  const placeholder = !members.length
    ? tr('ui.teamChat.addAgentsFirst')
    : empty ? tr('ui.teamChat.messageTheTeamPlaceholder') : tr('ui.teamChat.replyToTheTeam');
  const addMention = (m: Member) => {
    setInput((v) => `${v}${v && !v.endsWith(' ') ? ' ' : ''}@${m.handle} `);
    inputRef.current?.focus();
  };

  const composer = (
    <div className="relative mx-auto w-full max-w-3xl">
      {menuOpen && (
        <div id="mention-menu" role="listbox" aria-label={tr('ui.teamChat.mentionATeammate')} className="animate-rise absolute bottom-full left-0 right-0 z-20 mb-2 overflow-hidden rounded-xl border border-line bg-panel shadow-lg">
          <div className="border-b border-line px-3 py-1.5 text-[11.5px] text-fg-dim">{tr('ui.teamChat.mention')}</div>
          {options.map((m, i) => (
            <button
              key={m.id}
              id={`mention-opt-${i}`}
              role="option"
              aria-selected={i === mentionIndex}
              onMouseEnter={() => setMentionIndex(i)}
              onMouseDown={(e) => { e.preventDefault(); pickMention(m); }}
              className={cn('flex min-h-11 w-full items-center gap-2 border-l-2 px-3 py-2 text-left transition-colors md:min-h-0', i === mentionIndex ? 'border-l-accent bg-panel-2' : 'border-l-transparent hover:bg-panel-2')}
            >
              <span className="text-[13px] font-medium text-fg">{m.id === '*' ? tr('ui.teamChat.everyone') : m.name}</span>
              <span className="font-mono text-[11.5px] text-accent-ink">@{m.handle}</span>
              <span className="ml-auto truncate pl-3 text-[11.5px] text-fg-dim">{m.id === '*' ? tr('ui.teamChat.everyAgentAnswers') : m.lead ? tr('ui.teamChat.teamLead') : ''}</span>
            </button>
          ))}
        </div>
      )}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
        className={cn(
          'relative rounded-2xl border bg-panel shadow-[0_6px_24px_rgba(0,0,0,0.06)] transition-colors dark:shadow-[0_6px_24px_rgba(0,0,0,0.35)]',
          dragOver ? 'border-accent' : 'border-line focus-within:border-fg-dim',
        )}
      >
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 border-b border-line px-3 pb-1 pt-2.5">
            {attachments.map((a) => (
              <span key={a.id} className="inline-flex max-w-full items-center gap-1.5 rounded border border-line bg-panel-2 py-1 pl-1 pr-1.5 font-mono text-[10.5px]">
                {a.kind === 'image' && a.previewUrl ? (
                  <img src={a.previewUrl} alt="" className="h-8 w-8 flex-none rounded-sm object-cover" />
                ) : (
                  <svg className="h-4 w-4 flex-none text-fg-dim" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>
                )}
                <span className="truncate text-fg" title={a.name}>{a.name}</span>
                <button
                  type="button"
                  onClick={() => removeAttachment(a.id)}
                  aria-label={tr('ui.home.removeName', { name: a.name })}
                  className="grid h-11 w-11 flex-none place-items-center text-fg-dim transition-colors hover:text-danger-ink md:h-auto md:w-auto"
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        {voice.active && <VoiceBar voice={voice} />}
        <textarea
          ref={inputRef}
          rows={1}
          value={input}
          disabled={members.length === 0}
          onChange={(e) => { setInput(e.target.value); updateMention(e.target.value, e.target.selectionStart ?? e.target.value.length); }}
          onSelect={(e) => updateMention(e.currentTarget.value, e.currentTarget.selectionStart ?? 0)}
          onBlur={() => setMention(null)}
          onPaste={(e) => {
            // Pasted pictures become attachments; pasted text goes in as text.
            const pictures = Array.from(e.clipboardData.files || []).filter((f) => f.type.startsWith('image/'));
            if (pictures.length) { e.preventDefault(); addFiles(pictures); }
          }}
          onKeyDown={(e) => {
            if (menuOpen) {
              if (e.key === 'ArrowDown') { e.preventDefault(); setMentionIndex((i) => Math.min(i + 1, options.length - 1)); return; }
              if (e.key === 'ArrowUp') { e.preventDefault(); setMentionIndex((i) => Math.max(i - 1, 0)); return; }
              if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickMention(options[mentionIndex]); return; }
              if (e.key === 'Escape') { e.preventDefault(); setMention(null); return; }
            }
            if (e.key === 'Escape' && running) { e.preventDefault(); void stop(); return; }
            if (isSendKey(e, getSendKey())) { e.preventDefault(); void send(); }
          }}
          role="combobox"
          aria-label={tr('ui.teamChat.messageTheTeam')}
          aria-expanded={menuOpen}
          aria-controls="mention-menu"
          aria-activedescendant={menuOpen ? `mention-opt-${mentionIndex}` : undefined}
          placeholder={placeholder}
          className={cn('block min-h-11 max-h-48 w-full resize-none bg-transparent px-4 pb-1 pt-3.5 text-[15px] text-fg outline-none placeholder:text-fg-dim focus-visible:outline-none', voice.active && '!hidden')}
        />
        <div className={cn('flex min-w-0 items-center gap-0.5 px-2 pb-2 pt-1', voice.active && '!hidden')}>
          <button
            type="button"
            disabled={members.length === 0}
            onClick={() => fileInputRef.current?.click()}
            aria-label={tr('ui.composerMenu.attachFiles')}
            title={tr('ui.teamChat.attachPicturesOrDocuments')}
            className={composerIconBtn}
          >
            <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
          </button>
          <span className="flex min-w-0 flex-none items-center gap-1.5 pl-1 pr-1 text-[13px] font-medium text-fg-dim">
            <AgentBadge name={info.team.name} team />
            <span className="hidden max-w-[10rem] truncate sm:inline">{info.team.name}</span>
          </span>
          <button
            type="button"
            disabled={members.length === 0}
            onClick={() => {
              const el = inputRef.current;
              const value = `${input}${input && !input.endsWith(' ') ? ' ' : ''}@`;
              setInput(value);
              setMention({ start: value.length - 1, query: '' });
              setMentionIndex(0);
              requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(value.length, value.length); });
            }}
            title={tr('ui.teamChat.namePicksWhoAnswersAll')}
            className={composerBtn}
          >
            <span aria-hidden className="font-mono text-[15px] leading-none">@</span>
            <span>{tr('ui.teamChat.mentionSomeone')}</span>
          </button>
          <span className="flex-1" />
          {!voice.active && !running && <MicButton voice={voice} className="rounded-lg" />}
          <button
            onClick={() => { if (running) void stop(); else void send(); }}
            disabled={!running && !input.trim() && attachments.length === 0}
            aria-label={running ? tr('ui.teamChat.stopTheTeam') : tr('ui.teamChat.send')}
            className={cn(
              'grid h-11 w-11 flex-none place-items-center rounded-full transition disabled:cursor-not-allowed disabled:opacity-50 hover:brightness-105 md:h-8 md:w-8',
              running ? 'bg-danger text-bg' : 'bg-accent text-accent-foreground',
            )}
          >
            {running ? (
              <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1" /></svg>
            ) : (
              <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            )}
          </button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPT_ATTR}
          className="hidden"
          aria-label={tr('ui.composerMenu.attachFiles')}
          onChange={(e) => { addFiles(e.target.files || []); e.target.value = ''; }}
        />
      </div>
      {uploadError && <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-danger-ink">{uploadError}</div>}
      {sendError && <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-danger-ink">{sendError}</div>}
      <VoiceNotice voice={voice} />
      {empty && (
        <div className="mt-1.5 hidden items-center gap-x-4 px-2 text-[11px] text-fg-dim md:flex">
          <SendKeyHint />
        </div>
      )}
    </div>
  );

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 overflow-hidden bg-bg text-fg">
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <ChatHorizon className={cn(empty && 'hidden')} />
        {/* The same slim header as a chat with one agent: who, then the chat's name. */}
        <header className="app-page-header flex min-h-14 flex-none flex-row flex-nowrap items-center gap-2 border-b border-line py-2 pl-16 pr-3 md:pl-5">
          <div className="flex min-w-0 flex-1 flex-nowrap items-center gap-2.5 overflow-hidden">
            <Link to={`/teams/${teamId}`} className="flex flex-none items-center gap-2 rounded-lg hover:text-accent-ink" aria-label={tr('ui.teamChat.chattingWithTeam', { name: info.team.name })}>
              <AgentBadge name={info.team.name} team />
              <span className={cn('text-[14px] font-semibold', chatId && 'hidden sm:inline')}>{info.team.name}</span>
            </Link>
            <span aria-hidden className="hidden flex-none text-fg-dim sm:inline">/</span>
            <h1 className={cn('min-w-0 truncate text-[14px] text-fg-dim', chatId ? 'max-sm:font-semibold max-sm:text-fg' : 'max-sm:hidden')}>
              {chatId ? (activeChat?.title || tr('ui.teamChat.groupChat')) : tr('ui.teamChat.newChat')}
            </h1>
          </div>
          <div className="flex flex-none items-center gap-1" aria-label={tr('ui.teamChat.teamMembers')}>
            {members.slice(0, 5).map((m) => (
              <button
                key={m.id}
                type="button"
                title={m.lead ? tr('ui.teamChat.mentionNameLead', { name: m.name }) : tr('ui.teamChat.mentionName', { name: m.name })}
                aria-label={m.lead ? tr('ui.teamChat.mentionNameLead', { name: m.name }) : tr('ui.teamChat.mentionName', { name: m.name })}
                onClick={() => addMention(m)}
                className={cn(
                  'relative hidden h-9 items-center gap-1.5 rounded-full border px-2 text-[12.5px] transition-colors sm:inline-flex md:h-8',
                  speaker === m.name ? 'border-accent text-accent-ink' : 'border-line text-fg-dim hover:border-fg-dim hover:text-fg',
                )}
              >
                <span className="max-w-[7rem] truncate">{m.name}</span>
                {speaker === m.name && <span className="think-bars" aria-label={tr('ui.teamChat.replying')}><i /><i /><i /></span>}
              </button>
            ))}
            {members.length > 5 && <span className="hidden text-[12px] text-fg-dim sm:inline">+{members.length - 5}</span>}
            {chatId && (
              <button
                type="button"
                onClick={() => setParams({})}
                className="inline-flex h-11 items-center rounded-lg px-2.5 text-[12.5px] text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:h-9"
              >
                {tr('ui.teamChat.newChat')}
              </button>
            )}
          </div>
        </header>

        <div ref={scrollRef} role="log" aria-live="polite" aria-relevant="additions text" className="relative min-h-0 flex-1 overscroll-contain overflow-y-auto">
          {empty ? (
            <div className="mx-auto flex min-h-full w-full max-w-[720px] flex-col justify-center px-4 pb-16 pt-10 sm:px-6">
              <div className="flex flex-col items-center text-center">
                <AgentBadge name={info.team.name} team size="lg" />
                <h2 className="mt-4 text-[28px] font-bold leading-tight tracking-[-0.015em] sm:text-[30px]">{tr('ui.teamChat.talkToTheWholeTeam')}</h2>
                <p className="mt-2 max-w-md text-[15px] text-fg-dim">
                  {lead ? tr('ui.teamChat.leadAnswersUnlessYouMention', { name: lead.name }) : tr('ui.teamChat.oneAgentAnswersUnlessYouMention')}
                </p>
              </div>
              <div className="mt-6">{composer}</div>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {members.map((m) => (
                  <button key={m.id} type="button" onClick={() => addMention(m)}
                    className="min-h-11 rounded-full border border-line bg-panel px-3.5 text-[13px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:min-h-9">
                    {tr('ui.teamChat.askName', { name: m.name })}
                  </button>
                ))}
                {members.length > 1 && (
                  <button type="button" onClick={() => addMention(EVERYONE)}
                    className="min-h-11 rounded-full border border-line bg-panel px-3.5 text-[13px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:min-h-9">
                    {tr('ui.teamChat.askEveryone')}
                  </button>
                )}
              </div>
              <p className="mt-5 text-center text-[13px] text-fg-dim">
                {tr('ui.teamChat.orTalkToOneAgent')}{' '}
                <button type="button" onClick={() => navigate('/home?new=1')} className="underline-offset-2 hover:text-fg hover:underline">{tr('ui.teamChat.startAOneToOneChat')}</button>
              </p>
            </div>
          ) : (
            <div className="py-4">
              {messages.map((message, index) => (
                <ChatMessageRow
                  key={message.id}
                  message={message}
                  isLast={index === messages.length - 1}
                  streaming={running && index === messages.length - 1 && message.role === 'assistant'}
                />
              ))}
              {running && (
                <div role="status" className="mx-auto w-full max-w-3xl px-4 py-3 sm:px-6">
                  <div className="flex items-center gap-2.5 text-[13px] text-fg-dim">
                    <span className="think-bars"><i /><i /><i /><i /></span>
                    {status || (speaker ? tr('ui.teamChat.speakerIsWorking', { speaker }) : tr('ui.teamChat.pickingWhoAnswers'))}
                  </div>
                </div>
              )}
              {!running && status && (
                <p className="mx-auto w-full max-w-3xl px-4 py-3 text-[13px] text-fg-dim sm:px-6">{status}</p>
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
            <div key={a.request_id} role="alert" className="mx-auto my-4 w-[calc(100%-3rem)] max-w-[720px]">
              <PanelCard
                tone="danger"
                tag={<>{tr('ui.home.approval')}</>}
                title={<>{tr('ui.home.run')}{' '}<span className="text-accent-ink">{humanizeToolName(a.tool_name)}</span>?</>}
                subtitle={a.details || undefined}
                fields={[
                  ...(speaker ? [{ label: tr('ui.teamChat.askedBy'), value: speaker }] : []),
                  { label: tr('ui.home.tool'), value: humanizeToolName(a.tool_name) },
                  { label: tr('ui.home.risk'), value: <span className={a.risk_level === 'high' || a.risk_level === 'critical' ? 'text-danger-ink' : undefined}>{a.risk_level}</span> },
                  ...(a.app ? [{ label: tr('ui.home.app'), value: a.app.label }] : []),
                ]}
                footer={(
                  <>
                    <PanelButton kind="primary" onClick={() => respondApproval(a.request_id, true)}>{tr('ui.home.approve')}</PanelButton>
                    {!a.always_ask && <PanelButton onClick={() => respondApproval(a.request_id, true, true)}>{tr('ui.home.approveForSession')}</PanelButton>}
                    {a.full_shell && (
                      <PanelButton onClick={() => respondApproval(a.request_id, true, true, true)} title={tr('ui.home.runEveryShellCommandIn')}>{tr('ui.home.allowAllCommandsInThis')}</PanelButton>
                    )}
                    {a.app && (
                      <>
                        <PanelButton onClick={() => respondApproval(a.request_id, true, false, false, 'chat')} title={tr('ui.home.letAgentsClickAndType', { label: a.app.label })}>{tr('ui.home.allowLabelInThisChat', { label: a.app.label })}</PanelButton>
                        <PanelButton onClick={() => respondApproval(a.request_id, true, false, false, 'always')} title={tr('ui.home.neverAskAgainBeforeAgents', { label: a.app.label })}>{tr('ui.home.alwaysAllowLabel', { label: a.app.label })}</PanelButton>
                      </>
                    )}
                    {a.always && (
                      <PanelButton onClick={() => respondApproval(a.request_id, true, true, false, undefined, true)}
                        title={a.always.kind === 'command' ? tr('ui.approvals.alwaysCommandHint', { label: a.always.label }) : tr('ui.approvals.alwaysToolHint')}>
                        {a.always.kind === 'command' ? tr('ui.approvals.alwaysAllowCommand', { label: a.always.label }) : tr('ui.approvals.alwaysAllow')}
                      </PanelButton>
                    )}
                    <span className="grow" />
                    <PanelButton kind="danger" onClick={() => respondApproval(a.request_id, false)}>{tr('ui.home.deny')}</PanelButton>
                  </>
                )}
              >
                {a.params && Object.keys(a.params).length > 0 ? (
                  <pre className="max-h-40 overflow-auto rounded border border-line bg-panel-2 p-2 font-mono text-[11px] text-fg-dim">{JSON.stringify(a.params, null, 2)}</pre>
                ) : undefined}
              </PanelCard>
            </div>
          ))}
        </div>

        {!empty && (
          <div className="relative flex-none px-3 pb-4 pt-2 sm:px-6">
            {composer}
          </div>
        )}
      </div>
    </div>
  );
}
