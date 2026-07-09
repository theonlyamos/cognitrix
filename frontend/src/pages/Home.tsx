import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
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

// Tools are advertised to the model with spaces as underscores; show them back
// readable (e.g. "web_search" → "web search").
const humanizeTool = (name: string) => name.replace(/_/g, ' ');

// Re-indent a JSON string for display; leave non-JSON (or already-pretty) as-is.
const prettyJson = (s: string) => {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
};

const fmtTime = (t?: string | number) =>
  t ? new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '';

// Fenced code block with a hover copy button. Reads the rendered text from the
// DOM so it works regardless of the syntax-highlight token spans inside.
function CodeBlock({ children }: { children?: ReactNode }) {
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const text = ref.current?.innerText ?? '';
    void navigator.clipboard?.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="md-code">
      <button type="button" className="md-copy" onClick={copy}>{copied ? 'copied' : 'copy'}</button>
      <pre ref={ref}>{children}</pre>
    </div>
  );
}

// Route internal links (/tasks/… — multi-step replies carry a run link) through
// the SPA router; open external links in a new tab safely.
const mdComponents: Components = {
  a({ href, children }) {
    if (href && href.startsWith('/')) {
      return <Link to={href} className="text-accent-ink underline underline-offset-2 hover:brightness-110">{children}</Link>;
    }
    return (
      <a href={href} target="_blank" rel="noreferrer nofollow" className="text-accent-ink underline underline-offset-2 hover:brightness-110">
        {children}
      </a>
    );
  },
  pre({ children }) {
    return <CodeBlock>{children}</CodeBlock>;
  },
};

function MarkdownMessage({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
      components={mdComponents}
    >
      {content}
    </ReactMarkdown>
  );
}

interface ConvoSummary {
  id: string;
  title: string;
  datetime?: string;
  updated_at?: string;
  message_count: number;
}

const sessionKey = (agentId: string) => `chatSession:${agentId}`;

// ── Attachments ──
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_TOTAL_BYTES = 25 * 1024 * 1024;
const IMAGE_MAX_DIM = 1568; // downscale ceiling — plenty for vision, keeps payload small

// Accepted attachments: images + common document types. Keep DOC_EXT and
// ACCEPT_ATTR in sync — `accept` filters the file picker; addFiles re-checks so
// drag-drop/paste can't slip other types in.
const DOC_EXT = /\.(pdf|txt|md|markdown|csv|tsv|docx?|xlsx?|pptx?|rtf|odt|ods|odp|json|log)$/i;
const ACCEPT_ATTR = 'image/*,.pdf,.txt,.md,.markdown,.csv,.tsv,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.rtf,.odt,.ods,.odp,.json,.log';

interface Attachment {
  id: string;
  kind: 'image' | 'file';
  name: string;
  mime: string;
  dataUrl: string;
}

interface Skill {
  name: string;
  description: string;
  category?: string;
  argument_hint?: string;
  tags?: string[];
}

interface ApprovalRequest {
  request_id: string;
  tool_name: string;
  params?: Record<string, unknown>;
  risk_level: string;
  categories?: string[];
  details?: string;
}

const readFileAsDataURL = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });

// Downscale large images client-side (long edge ≤ IMAGE_MAX_DIM) so uploads and
// vision-token cost stay small; falls back to the original on any failure.
const downscaleImage = async (file: File): Promise<string> => {
  const dataUrl = await readFileAsDataURL(file);
  return new Promise<string>((resolve) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, IMAGE_MAX_DIM / Math.max(img.width, img.height));
      if (scale >= 1) return resolve(dataUrl);
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      const ctx = canvas.getContext('2d');
      if (!ctx) return resolve(dataUrl);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      resolve(canvas.toDataURL('image/jpeg', 0.9));
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
};

// Approximate decoded size of a data URL (base64 payload → bytes).
const dataUrlBytes = (dataUrl: string) => Math.floor(((dataUrl.split(',')[1] || '').length * 3) / 4);

export default function Home() {
  const { messages, addMessage, appendToLastMessage, setIsStreaming, addToolCall, resolveToolCall, clearMessages, setMessages } = useSession();
  const [input, setInput] = useState('');
  const [waiting, setWaiting] = useState(false); // sent, before first token
  const [streaming, setStreaming] = useState(false); // actively streaming a reply
  const [planning, setPlanning] = useState<string | null>(null); // transient status (e.g. multi-step)
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Composer attachments (images → vision, other files → agent workspace).
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  // Slash-skills menu: typing `/name` shows matching skills to insert. The message
  // is sent as normal text — the agent loads the skill via its load_skill tool.
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [skillIndex, setSkillIndex] = useState(0);
  const [menuHidden, setMenuHidden] = useState(false);
  // The `/token` under the cursor (anywhere in the message), or null.
  const [slash, setSlash] = useState<{ query: string; start: number } | null>(null);
  const slashRef = useRef(slash);
  slashRef.current = slash;

  // A slash command is the word under the cursor that starts with `/` — it may
  // sit at the start, middle, or end of the message.
  const updateSlash = useCallback((value: string, cursor: number) => {
    const m = value.slice(0, cursor).match(/(?:^|\s)\/(\S*)$/);
    setSlash(m ? { query: m[1].toLowerCase(), start: cursor - m[1].length - 1 } : null);
  }, []);

  useEffect(() => {
    if (input.includes('/') && skills === null) {
      // Guard: a proxy/error page could return non-array data — never let that
      // crash the composer (filteredSkills would throw on .filter).
      api.get('/skills')
        .then((r) => setSkills(Array.isArray(r.data) ? r.data : []))
        .catch(() => setSkills([]));
    }
  }, [input, skills]);

  const skillQuery = slash?.query ?? null;

  const filteredSkills = useMemo(() => {
    if (skillQuery === null || !skills) return [] as Skill[];
    const startsWith = (n: string) => (n.toLowerCase().startsWith(skillQuery) ? 0 : 1);
    // No cap — a bare "/" lists every skill; the menu scrolls (max-h-64).
    return skills
      .filter((s) => s.name.toLowerCase().includes(skillQuery))
      .sort((a, b) => startsWith(a.name) - startsWith(b.name) || a.name.localeCompare(b.name));
  }, [skills, skillQuery]);

  const skillMenuOpen = !menuHidden && skillQuery !== null && filteredSkills.length > 0;
  useEffect(() => { setSkillIndex(0); }, [skillQuery]);
  // Keep the highlighted option visible as you arrow through a long list.
  useEffect(() => {
    if (skillMenuOpen) document.getElementById(`skill-opt-${skillIndex}`)?.scrollIntoView({ block: 'nearest' });
  }, [skillIndex, skillMenuOpen]);

  const selectSkill = useCallback((s: Skill) => {
    const sl = slashRef.current;
    const prev = inputRef.current?.value ?? '';
    const start = sl ? sl.start : 0;
    let end = start + 1;
    while (end < prev.length && !/\s/.test(prev[end])) end++;
    const insert = `/${s.name} `;
    const caret = start + insert.length;
    setInput(prev.slice(0, start) + insert + prev.slice(end));
    setMenuHidden(false);
    setSlash(null);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) { el.focus(); el.selectionStart = el.selectionEnd = caret; }
    });
  }, []);

  // Web tool-approval prompts + a per-browser bypass (auto-approve) toggle.
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [bypass, setBypass] = useState(() => localStorage.getItem('bypassPermissions') === '1');
  const bypassRef = useRef(bypass);
  bypassRef.current = bypass;

  const respondApproval = useCallback(async (requestId: string, approved: boolean, remember = false) => {
    setApprovals((prev) => prev.filter((a) => a.request_id !== requestId));
    try {
      await api.post('/agents/approval', { request_id: requestId, approved, remember });
    } catch {
      /* best-effort — the turn times out server-side if this never lands */
    }
  }, []);

  const toggleBypass = useCallback(() => {
    setBypass((b) => {
      const next = !b;
      localStorage.setItem('bypassPermissions', next ? '1' : '0');
      return next;
    });
  }, []);

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

  // Auto-grow the composer to fit multi-line input (capped by max-h-40 → scroll).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const addFiles = useCallback(async (fileList: FileList | File[]) => {
    const incoming = Array.from(fileList);
    if (incoming.length === 0) return;
    setUploadError(null);
    let total = attachmentsRef.current.reduce((sum, a) => sum + dataUrlBytes(a.dataUrl), 0);
    const next: Attachment[] = [];
    for (const file of incoming) {
      // Pasted/dropped files often arrive with an empty MIME type — derive it
      // from the extension so images still route to vision (not the workspace),
      // and re-tag the file so the thumbnail + downscale actually work.
      const ext = /\.(png|jpe?g|gif|webp|bmp|avif)$/i.exec(file.name)?.[1]?.toLowerCase();
      const imgMime = file.type.startsWith('image/') ? file.type : ext ? `image/${ext === 'jpg' ? 'jpeg' : ext}` : '';
      const isImage = !!imgMime;
      const isDoc = DOC_EXT.test(file.name) || /(pdf|word|excel|spreadsheet|presentation|officedocument|^text\/|rtf|csv|json)/i.test(file.type);
      if (!isImage && !isDoc) {
        setUploadError('Only images and documents can be attached.');
        continue;
      }
      const src = isImage && !file.type ? new File([file], file.name, { type: imgMime }) : file;
      let dataUrl: string;
      try {
        dataUrl = isImage ? await downscaleImage(src) : await readFileAsDataURL(file);
      } catch {
        continue;
      }
      const bytes = dataUrlBytes(dataUrl);
      if (bytes > MAX_FILE_BYTES) {
        setUploadError(`${file.name} is larger than 10 MB.`);
        continue;
      }
      if (total + bytes > MAX_TOTAL_BYTES) {
        setUploadError('Attachments exceed 25 MB total.');
        break;
      }
      total += bytes;
      next.push({
        id: crypto.randomUUID(),
        kind: isImage ? 'image' : 'file',
        name: file.name || 'file',
        mime: imgMime || file.type || 'application/octet-stream',
        dataUrl,
      });
    }
    if (next.length) setAttachments((prev) => [...prev, ...next]);
  }, []);

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const resetThreadState = useCallback(() => {
    clearMessages();
    setWaiting(false);
    setStreaming(false);
    setPlanning(null);
    setApprovals([]);
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
    (event: { type: string; content?: string; action?: string; session_id?: string; tool_name?: string; status?: string; tool_call_id?: string; params?: string; result?: string }) => {
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
        case 'tool':
          // The agent invoked a tool — show it running with its params, then flip
          // to done/error and attach the result.
          if (event.tool_name) {
            if (event.status === 'started')
              addToolCall(event.tool_name, { id: event.tool_call_id, params: event.params });
            else
              resolveToolCall(event.tool_name, event.status === 'error' ? 'error' : 'done', { id: event.tool_call_id, result: event.result });
          }
          break;
        case 'approval_request': {
          const req = event as unknown as ApprovalRequest;
          if (req.request_id) {
            setApprovals((prev) =>
              prev.some((a) => a.request_id === req.request_id) ? prev : [...prev, req],
            );
          }
          break;
        }
        default:
          break;
      }
    },
    [appendToLastMessage, addMessage, addToolCall, resolveToolCall, agentId, refetchConvos],
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
      const pending = attachmentsRef.current;
      if ((!msg && pending.length === 0) || waiting) return;
      // Keep a record of what was attached in the visible user message.
      const shown = pending.length
        ? `${msg}${msg ? '\n' : ''}📎 ${pending.map((a) => a.name).join(', ')}`
        : msg;
      addMessage('user', shown);
      setInput('');
      setAttachments([]);
      setUploadError(null);
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
          ...(pending.length
            ? { attachments: pending.map((a) => ({ kind: a.kind, name: a.name, mime: a.mime, dataUrl: a.dataUrl })) }
            : {}),
          ...(bypassRef.current ? { bypass_permissions: true } : {}),
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
                // Tool-activity row: the agent's tool calls as status chips.
                if (m.role === 'tool') {
                  if (!m.tools?.length) return null;
                  return (
                    <div key={m.id} className="grid grid-cols-[68px_1fr] gap-4 border-b border-line px-6 py-3">
                      <div className="pt-0.5 font-mono text-[11px] tracking-[0.06em] text-fg-dim">TOOL</div>
                      <div className="flex flex-col items-start gap-1.5">
                        {m.tools.map((t, ti) => (
                          // Collapsible: summary is the status chip; expands to the
                          // call's params and result. Native <details> = free
                          // keyboard/toggle handling, open state kept across the
                          // running→done re-render.
                          <details key={t.id || ti} className="group w-full max-w-2xl">
                            <summary className="inline-flex list-none cursor-pointer select-none items-center gap-1.5 rounded border border-line bg-panel-2 px-2 py-1 font-mono text-[11px] transition-colors hover:border-fg-dim [&::-webkit-details-marker]:hidden">
                              {t.status === 'running' ? (
                                <span className="think-bars"><i /><i /><i /></span>
                              ) : t.status === 'error' ? (
                                <span className="text-danger-ink" aria-hidden>✗</span>
                              ) : (
                                <span className="text-accent-ink" aria-hidden>✓</span>
                              )}
                              <span className={cn(t.status === 'running' ? 'text-accent-ink' : 'text-fg')}>
                                {humanizeTool(t.name)}
                              </span>
                              {t.status === 'running' && <span className="text-fg-dim">running…</span>}
                              <svg className="ml-0.5 text-fg-dim transition-transform group-open:rotate-90" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M9 6l6 6-6 6" /></svg>
                            </summary>
                            <div className="mt-1.5 space-y-2 rounded border border-line bg-panel-2 p-2 font-mono text-[11px]">
                              {t.params && t.params !== '{}' && (
                                <div>
                                  <div className="mb-1 text-[10px] uppercase tracking-wider text-fg-dim">params</div>
                                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-fg-dim">{prettyJson(t.params)}</pre>
                                </div>
                              )}
                              <div>
                                <div className="mb-1 text-[10px] uppercase tracking-wider text-fg-dim">result</div>
                                {t.status === 'running' ? (
                                  <span className="text-fg-dim">running…</span>
                                ) : (
                                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-fg-dim">{t.result || '(no output)'}</pre>
                                )}
                              </div>
                            </div>
                          </details>
                        ))}
                      </div>
                    </div>
                  );
                }
                // Drop empty assistant placeholders left between tool rounds
                // (an empty streaming chunk creates no text but can leave a shell).
                if (m.role === 'assistant' && !m.content.trim() && !(isLast && streaming)) return null;
                return (
                  <div key={m.id} className="grid grid-cols-[68px_1fr] gap-4 border-b border-line px-6 py-4">
                    <div className={cn('pt-0.5 font-mono text-[11px] tracking-[0.06em]', isUser ? 'text-accent-ink' : 'text-fg-dim')}>
                      {isUser ? 'YOU' : 'AGENT'}
                    </div>
                    <div className="min-w-0">
                      {isUser ? (
                        <div className="whitespace-pre-wrap break-words leading-relaxed">{m.content}</div>
                      ) : isLast && streaming ? (
                        // Render plain text while streaming; swap to markdown on completion
                        // so we don't re-parse the whole reply on every token.
                        <div className="whitespace-pre-wrap break-words leading-relaxed">
                          {m.content}
                          <span className="caret" />
                        </div>
                      ) : (
                        <div className="md break-words">
                          <MarkdownMessage content={m.content} />
                        </div>
                      )}
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

          {approvals.map((a) => (
            <div key={a.request_id} className="animate-rise mx-6 my-4 border-l-2 border-danger bg-danger/5 px-3 py-3 font-mono text-[12px]">
              <div className="flex items-center gap-2">
                <span className="text-danger-ink">⚠ approval required</span>
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
                <button onClick={() => respondApproval(a.request_id, true)} className="rounded border border-line px-2.5 py-1 text-[11px] text-fg-dim transition-colors hover:border-accent hover:text-accent-ink">approve</button>
                <button onClick={() => respondApproval(a.request_id, true, true)} className="rounded border border-line px-2.5 py-1 text-[11px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg">approve for session</button>
                <button onClick={() => respondApproval(a.request_id, false)} className="rounded border border-line px-2.5 py-1 text-[11px] text-fg-dim transition-colors hover:border-danger hover:text-danger-ink">deny</button>
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>

        {/* Composer */}
        <div className="flex-none border-t border-line px-6 py-4">
          <div className="relative mx-auto max-w-3xl">
            {skillMenuOpen && (
              <div
                id="skill-menu"
                role="listbox"
                className="animate-rise absolute bottom-full left-0 right-0 z-20 mb-2 overflow-hidden rounded-md border border-line bg-panel shadow-lg"
              >
                <div className="border-b border-line px-3 py-1.5 font-mono text-[10px] tracking-[0.18em] text-fg-dim">SKILLS</div>
                <div className="max-h-64 overflow-y-auto">
                  {filteredSkills.map((s, i) => (
                    <button
                      key={s.name}
                      id={`skill-opt-${i}`}
                      role="option"
                      aria-selected={i === skillIndex}
                      onMouseEnter={() => setSkillIndex(i)}
                      onClick={() => selectSkill(s)}
                      className={cn(
                        'flex w-full items-baseline gap-2 border-l-2 px-3 py-2 text-left transition-colors',
                        i === skillIndex ? 'border-l-accent bg-panel-2' : 'border-l-transparent hover:bg-panel-2',
                      )}
                    >
                      <span className="font-mono text-[11px] text-fg-dim">›_</span>
                      <span className="flex-none font-mono text-[12px] text-accent-ink">/{s.name}</span>
                      {s.argument_hint && <span className="flex-none font-mono text-[10.5px] text-fg-dim">{s.argument_hint}</span>}
                      <span className="ml-auto truncate pl-3 text-[11px] text-fg-dim">{s.description}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => { e.preventDefault(); setDragOver(false); void addFiles(e.dataTransfer.files); }}
              className={cn(
                'relative rounded-md border bg-panel transition-colors',
                // subtle 1px accent border on focus/drag — no glow ring
                dragOver ? 'border-accent' : 'border-line focus-within:border-accent',
              )}
            >
              {attachments.length > 0 && (
                <div className="flex flex-wrap gap-1.5 border-b border-line px-3 pb-1 pt-2.5">
                  {attachments.map((a) => (
                    <span
                      key={a.id}
                      className="group inline-flex max-w-full items-center gap-1.5 rounded border border-line bg-panel-2 py-1 pl-1 pr-1.5 font-mono text-[10.5px]"
                    >
                      {a.kind === 'image' ? (
                        <img src={a.dataUrl} alt="" className="h-5 w-5 flex-none rounded-sm object-cover" />
                      ) : (
                        <svg className="h-4 w-4 flex-none text-fg-dim" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>
                      )}
                      <span className="truncate text-fg" title={a.name}>{a.name}</span>
                      <button
                        onClick={() => removeAttachment(a.id)}
                        aria-label={`Remove ${a.name}`}
                        className="flex-none text-fg-dim transition-colors hover:text-danger-ink"
                      >
                        ✕
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <textarea
                ref={inputRef}
                rows={1}
                value={input}
                onChange={(e) => { setInput(e.target.value); setMenuHidden(false); updateSlash(e.target.value, e.target.selectionStart ?? e.target.value.length); }}
                onSelect={(e) => updateSlash(e.currentTarget.value, e.currentTarget.selectionStart ?? 0)}
                onPaste={(e) => {
                  // Paste images (screenshots, copied pictures) straight into attachments;
                  // let text paste through untouched.
                  const imgs = Array.from(e.clipboardData.files || []).filter((f) => f.type.startsWith('image/'));
                  if (imgs.length) { e.preventDefault(); void addFiles(imgs); }
                }}
                onKeyDown={(e) => {
                  if (skillMenuOpen) {
                    if (e.key === 'ArrowDown') { e.preventDefault(); setSkillIndex((i) => Math.min(i + 1, filteredSkills.length - 1)); return; }
                    if (e.key === 'ArrowUp') { e.preventDefault(); setSkillIndex((i) => Math.max(i - 1, 0)); return; }
                    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); selectSkill(filteredSkills[skillIndex]); return; }
                    if (e.key === 'Escape') { e.preventDefault(); setMenuHidden(true); return; }
                  }
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    send(input);
                  }
                }}
                role="combobox"
                aria-expanded={skillMenuOpen}
                aria-controls="skill-menu"
                aria-activedescendant={skillMenuOpen ? `skill-opt-${skillIndex}` : undefined}
                placeholder="Message the agent…"
                className="block max-h-40 w-full resize-none bg-transparent px-3 py-2.5 pr-12 text-sm text-fg outline-none placeholder:text-fg-dim focus:outline-none focus-visible:shadow-none"
              />
              <button
                onClick={() => send(input)}
                disabled={(!input.trim() && attachments.length === 0) || waiting}
                aria-label="Send"
                className="absolute bottom-1 right-1.5 grid h-8 w-8 place-items-center rounded bg-accent text-accent-foreground transition disabled:cursor-not-allowed disabled:opacity-40 hover:brightness-105"
              >
                {waiting ? (
                  <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" className="opacity-25" /><path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" /></svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 12l16-8-6 16-3-6-7-2z" /></svg>
                )}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ACCEPT_ATTR}
                className="hidden"
                onChange={(e) => { void addFiles(e.target.files || []); e.target.value = ''; }}
              />
            </div>
            {uploadError && (
              <div className="mt-1.5 font-mono text-[10.5px] text-danger-ink">{uploadError}</div>
            )}
            <div className="mt-2 flex items-center gap-4 font-mono text-[10.5px] text-fg-dim">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                aria-label="Attach files"
                className="flex items-center gap-1.5 transition-colors hover:text-fg"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
                attach
              </button>
              <button
                type="button"
                onClick={toggleBypass}
                aria-pressed={bypass}
                title="Auto-approve tool calls for this browser (skips the approval prompt). Use with care."
                className={cn('flex items-center gap-1 transition-colors', bypass ? 'text-danger-ink' : 'hover:text-fg')}
              >
                <span>[{bypass ? '✓' : 'x'}]</span> auto-approve
              </button>
              <span className="ml-auto"><kbd className="rounded border border-line px-1">⏎</kbd> send</span>
              <span><kbd className="rounded border border-line px-1">⇧⏎</kbd> newline</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
