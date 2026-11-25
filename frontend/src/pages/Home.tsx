import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChatHorizon } from '@/components/ThemeArtwork';
import { ChatMessageRow } from '@/components/ChatMessageRow';
import { WaitingMessages, type WaitingMessage } from '@/components/WaitingMessages';
import { useSession, type ChatMessage, type ToolArtifact, type ToolUse } from '@/context/SessionContext';
import { useSSE, type SSEEvent } from '@/hooks/useSSE';
import { useEventStream } from '@/hooks/useEventStream';
import { fetchChatRuns, useChatRuns } from '@/hooks/useChatRuns';
import { useResource } from '@/hooks/useResource';
import { api, errorMessage } from '@/lib/api';
import { desktop } from '@/lib/desktop';
import { useOptionalNotifications } from '@/context/NotificationsContext';
import { AGENTS_CHANGED_EVENT, hasFastMode } from '@/lib/providers';
import { cn } from '@/lib/utils';
import { parseChatEntries, toChatMessages } from '@/lib/transcript';
import { Button } from '@/lib/components/ui/button';
import { ComputerPanel } from '@/components/ComputerPanel';
import { ArtifactViewer } from '@/components/artifacts/ArtifactViewer';
import { AppViewPanel } from '@/components/appviews/AppViewPanel';
import { announceAppViewUpdate, appViewFrom, listAppViews, onOpenAppView, type AppViewSummary } from '@/lib/appViews';
import { onCardAction, parseCard, requestCardAction } from '@/lib/cards';
import { copyBackMessage, useChatChanges, type ChangeSource } from '@/lib/fileChanges';
import { ChangesCards } from '@/components/changes/ChangesCard';
import { ChangesPanel } from '@/components/changes/ChangesPanel';
import { FileViewer } from '@/components/files/FileViewer';
import { listFiles, onOpenFile, parseFileRefs, type ProducedFileDetails } from '@/lib/producedFiles';
import { PanelButton, PanelCard } from '@/components/cards/PanelCard';
import { AccessRequestCard, type AccessGrant, type AccessRequest } from '@/components/AccessRequestCard';
import { announceArtifactUpdate, onArtifactUpdate, onOpenArtifact, parseStamp, publishedFrom, type ArtifactList } from '@/lib/artifactPages';
import { desktopName, getChatSandbox, sandboxStatus, setChatSandbox, type ChatSandbox, type SandboxDesktop } from '@/lib/sandbox';
import { ChatSidebar, loadSidebarOpen, saveSidebarOpen, type SidebarActivity, type SidebarWaiting } from '@/components/ChatSidebar';
import { QuestionCard, type QuestionAnswer, type QuestionRequest } from '@/components/QuestionCard';
import { MicButton, VoiceBar, VoiceNotice, useVoiceInput } from '@/components/VoiceInput';
import { LiveVoiceButton, LiveVoicePanel } from '@/components/LiveVoice';
import { useLiveVoice } from '@/hooks/useLiveVoice';
import { SideQuestionButton, SideQuestionsPanel } from '@/components/SideQuestions';
import { insertTranscript } from '@/lib/voice';
import FolderChips from '@/components/FolderChips';
import ComposerMenu from '@/components/ComposerMenu';
import { SwarmCard } from '@/components/SwarmCard';
import { useChatSwarms } from '@/lib/swarms';
import ChatModelPicker, { type ChatModel } from '@/components/ChatModelPicker';
import { getChatFolders, setChatFolders, type WorkFolder } from '@/lib/folders';
import { getSendKey, isSendKey } from '@/lib/preferences';
import { SendKeyHint } from '@/components/SendKeyHint';
import { AgentBadge } from '@/components/StartChat';
import { AgentPickerChip, NewChatGreeting, newChatSuggestions } from '@/components/NewChatGreeting';
import { useOptionalUser } from '@/context/AppContext';
import { agentModel, type PickTarget, type PickerAgent, type PickerTeam } from '@/lib/agentPicker';
import { takeChatAutoSend, takeChatDraft } from '@/lib/chatDraft';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { CHATS_EDITED, chatHref, chatsStale, newChatHref, setChatNav, type ChatsEditedDetail } from '@/lib/chatNav';
import { getSpeechPrefs, speakText, stopSpeaking } from '@/lib/speech';
import { setViewingChat } from '@/lib/chatPresence';
import { useApprovalSeen } from '@/hooks/useApprovalSeen';
import { useIsPhone } from '@/hooks/useIsPhone';
import { ACCEPT_ATTR, DOC_EXT, MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, type Attachment } from '@/lib/chatAttachments';
import { tr, dateLocale } from '@/lib/i18n';
import ComputerChip from '@/components/ComputerChip';
import { PcScreenPanel } from '@/components/PcScreen';
import { getChatComputer, listDevices, setChatComputer, type PairedDevice } from '@/lib/devices';
import { getProject, listProjects, projectSwatch, type Project } from '@/lib/projects';
import { ChillScreen, ModeSwitch, type ChillRailItem } from '@/components/chill/ChillScreen';
import { repliesOf, splitTurns, type ChillTurn } from '@/lib/chillTurns';
import { FileCards } from '@/components/files/FileCards';
import { ChillPageCard } from '@/components/chill/ChillPageCard';
import { madeInTurn, usedComputer } from '@/lib/chillMade';
import { ComputerPreview } from '@/components/ComputerPreview';
import { InfoCard } from '@/components/cards/InfoCard';
import { FilesShelf } from '@/components/files/FilesShelf';
import { CHILL_PATH, isModeSwitchKey, setUiMode } from '@/lib/uiMode';


interface ConvoSummary {
  id: string;
  title: string;
  datetime?: string;
  updated_at?: string;
  message_count: number;
  /** Set when an agent started this chat. */
  started_by?: { agent_id: string; agent_name: string; session_id?: string | null } | null;
  pinned?: boolean;
  archived?: boolean;
}

/** An earlier message being reworded; sending replaces it and what followed. */
interface Rework {
  turn: number;
  index: number;
  expected: string;
  /** The turn was sent with files or images: keep it and them, redo the answer. */
  keep?: boolean;
}

/** The note the server keeps after a turn with uploaded files. */
const UPLOAD_NOTE = '[User uploaded files, readable with your file tools:]';
const isUploadNote = (m: ChatMessage) => m.role === 'user' && m.content.startsWith(UPLOAD_NOTE);
const isUserTurn = (m: ChatMessage) => m.role === 'user' && !m.steered && !!m.content.trim() && !isUploadNote(m);
/** A turn sent with files or images can't be resent: the resend would lose them. */
const carriesUploads = (all: ChatMessage[], index: number) => {
  const message = all[index];
  const next = all[index + 1];
  return !!message?.artifacts?.length || /(^|\n)📎 /.test(message?.content || '')
    || (!!next && next.role === 'user' && (isUploadNote(next) || !next.content.trim()));
};

/** One row of the combined chat list: a chat with an agent or a team. */
interface ChatSummary extends ConvoSummary {
  kind: 'agent' | 'team';
  agent_id: string | null;
  team_id: string | null;
  running?: boolean;
  project_id?: string | null;
}

const sessionKey = (agentId: string) => `chatSession:${agentId}`;
/** A screen wide enough for panels beside the chat (Tailwind's md). */
const isWideScreen = () => typeof window === 'undefined' || !window.matchMedia || window.matchMedia('(min-width: 768px)').matches;
const STOP_RECONCILE_DELAY_MS = 4_000;
// Quiet seconds in the middle of a reply before "Still working…" shows.
const QUIET_AFTER_MS = 4_000;

// ── Attachments ──
/** Id prefix for messages held on the page until a new chat has its id. */
const HELD_PREFIX = 'held-';

type EditSource =
  | { type: 'artifact'; artifact: ToolArtifact }
  | { type: 'attachment'; attachmentId: string };

interface Skill {
  name: string;
  description: string;
  category?: string;
  argument_hint?: string;
  tags?: string[];
}

const humanizeToolName = (name: string) => name.replace(/_/g, ' ');

interface ApprovalRequest {
  request_id: string;
  tool_name: string;
  params?: Record<string, unknown>;
  risk_level: string;
  categories?: string[];
  details?: string;
  /** A full-shell command: the card offers "allow all commands in this chat". */
  full_shell?: boolean;
  /** A background action in an app window: the card offers to stop asking about that app. */
  app?: { key: string; label: string };
  /** Takes over the screen: asked every time, so nothing to remember. */
  always_ask?: boolean;
  /** "Always allow": the tool for this agent, or commands starting like this one on this computer. */
  always?: { kind: 'tool' | 'command'; label: string; computer?: string };
  /** Request Access: the agent asks for a tool or connected app it doesn't have. */
  access?: AccessRequest & { agent?: string };
  /** The agent asks you to take over its computer (Ask To Take Over). */
  takeover?: { desktop: string; reason: string };
}

export default function Home({ chill = false }: { chill?: boolean } = {}) {
  const chillRef = useRef(chill);
  chillRef.current = chill;
  const { messages, addMessage, attachArtifactsToLatestUser: attachArtifactsToLatestUserFromSession, addArtifactsToLastUser, appendToLastMessage, setIsStreaming, addToolCall, resolveToolCall, attachFilesMade, stopRunningTools, failRunningTools, markTurnDuration, clearMessages, setMessages } = useSession();
  const attachArtifactsToLatestUser = attachArtifactsToLatestUserFromSession ?? addArtifactsToLastUser;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const user = useOptionalUser();
  const [input, setInput] = useState(takeChatDraft);
  const [waiting, setWaiting] = useState(false); // sent, before first token
  const [streaming, setStreaming] = useState(false); // actively streaming a reply
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const [planning, setPlanning] = useState<string | null>(null); // transient status (e.g. multi-step)
  const [rework, setRework] = useState<Rework | null>(null);
  // Sent while the agent works; read after its current step (steering).
  const [waitingMsgs, setWaitingMsgs] = useState<WaitingMessage[]>([]);
  const [steerError, setSteerError] = useState<string | null>(null);
  // Messages the agent never got to (the turn ended first): sent next.
  const resendRef = useRef<string | null>(null);
  // Bumped to make the resend effect look at resendRef again.
  const [resendTick, setResendTick] = useState(0);
  const [reworkError, setReworkError] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isPhone = useIsPhone();

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

  // Composer attachments (images → vision, other files → agent workspace).
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [editSource, setEditSource] = useState<EditSource | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const attachmentsRef = useRef(attachments);
  const ownedPreviewUrlsRef = useRef(new Set<string>());
  attachmentsRef.current = attachments;

  const revokePreviewUrl = useCallback((url?: string) => {
    if (!url || !ownedPreviewUrlsRef.current.delete(url)) return;
    URL.revokeObjectURL(url);
  }, []);

  useEffect(() => () => {
    for (const url of ownedPreviewUrlsRef.current) URL.revokeObjectURL(url);
    ownedPreviewUrlsRef.current.clear();
  }, []);

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
  const [question, setQuestion] = useState<QuestionRequest | null>(null);
  const [bypass, setBypass] = useState(() => localStorage.getItem('bypassPermissions') === '1');
  const bypassRef = useRef(bypass);
  bypassRef.current = bypass;
  // Swarm: the agent splits the next messages' jobs across parallel workers.
  const [swarmOn, setSwarmOn] = useState(false);
  const swarmRef = useRef(swarmOn);
  swarmRef.current = swarmOn;
  // Reasoning level for this chat; '' keeps the agent's own setting.
  const [reasoning, setReasoning] = useState(() => {
    try { return localStorage.getItem('chatReasoningEffort') ?? ''; } catch { return ''; }
  });
  const reasoningRef = useRef(reasoning);
  reasoningRef.current = reasoning;
  const changeReasoning = (value: string) => {
    setReasoning(value);
    try { localStorage.setItem('chatReasoningEffort', value); } catch { /* not persisted */ }
  };

  useApprovalSeen(approvals);

  const respondApproval = useCallback(async (requestId: string, approved: boolean, remember = false, rememberShell = false, rememberApp?: 'chat' | 'always', always = false, grant?: AccessGrant) => {
    setApprovals((prev) => prev.filter((a) => a.request_id !== requestId));
    try {
      await api.post('/agents/approval', { request_id: requestId, approved, remember, ...(rememberShell ? { remember_shell: true } : {}), ...(rememberApp ? { remember_app: rememberApp } : {}), ...(always ? { always: true } : {}), ...(grant ? { grant } : {}) });
    } catch {
      /* best-effort — the turn times out server-side if this never lands */
    }
  }, []);

  const postQuestionAction = useCallback(async (
    current: QuestionRequest,
    action: 'answer' | 'cancel' | 'stop_timer',
    answer?: QuestionAnswer,
  ) => {
    try {
      await api.post('/agents/question', {
        request_id: current.request_id,
        action,
        ...answer,
      });
      if (action === 'stop_timer') {
        setQuestion((pending) => pending?.request_id === current.request_id
          ? { ...pending, auto_submit_seconds: null, auto_submit_at: null }
          : pending);
      } else {
        setQuestion((pending) => pending?.request_id === current.request_id ? null : pending);
        setWaiting(true);
      }
    } catch (error) {
      const status = (error as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        setQuestion((pending) => pending?.request_id === current.request_id ? null : pending);
        setWaiting(true);
        return;
      }
      throw error;
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
  const { data: agentsData, loading: agentsLoading, refetch: refetchAgents } = useResource<(PickerAgent & { folders?: WorkFolder[] | null; run_in_sandbox?: boolean; code_on_my_computer?: boolean })[]>('/agents');
  const agents = useMemo(() => (agentsData || []).filter((a) => a.id), [agentsData]);
  // The agents' sandboxed computer (one desktop per agent), when the server has it on.
  const [sandboxOn, setSandboxOn] = useState(false);
  const [computerOpen, setComputerOpen] = useState(false);
  // Take over on an agent's request: the computer panel opens with you in control of this desktop.
  const [takeOverRequest, setTakeOverRequest] = useState<{ desktop: string; at: number } | null>(null);
  // The artifact open beside the chat (same place as the computer panel), and
  // whether it is expanded to fill the window.
  const [artifactOpen, setArtifactOpen] = useState<string | null>(null);
  const [artifactExpanded, setArtifactExpanded] = useState(false);
  // A connected app's own screen open beside the chat (same place again).
  const [appViewOpen, setAppViewOpen] = useState<string | null>(null);
  const [appViewExpanded, setAppViewExpanded] = useState(false);
  // The chat's file changes open beside the chat (same place again), at a
  // source and file when opened from one.
  const [changesOpen, setChangesOpen] = useState<{ source?: string; path?: string } | null>(null);
  const [changesExpanded, setChangesExpanded] = useState(false);
  // A file an agent made, open beside the chat (same place again).
  const [fileOpen, setFileOpen] = useState<string | null>(null);
  const [fileExpanded, setFileExpanded] = useState(false);
  const closeFile = useCallback(() => {
    setFileOpen(null);
    setFileExpanded(false);
  }, []);
  const closeChanges = useCallback(() => {
    setChangesOpen(null);
    setChangesExpanded(false);
  }, []);
  const closeAppView = useCallback(() => {
    setAppViewOpen(null);
    setAppViewExpanded(false);
  }, []);
  const showArtifact = useCallback((id: string) => {
    setComputerOpen(false);
    setFileOpen(null);
    setAppViewOpen(null);
    setChangesOpen(null);
    setArtifactOpen(id);
    setNewPageIds((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);
  const closeArtifact = useCallback(() => {
    setArtifactOpen(null);
    setArtifactExpanded(false);
  }, []);
  useEffect(() => onOpenArtifact(showArtifact), [showArtifact]);
  const showFile = useCallback((id: string) => {
    setComputerOpen(false);
    setArtifactOpen(null);
    setArtifactExpanded(false);
    setAppViewOpen(null);
    setChangesOpen(null);
    setFileOpen(id);
  }, []);
  useEffect(() => onOpenFile(showFile), [showFile]);
  useEffect(() => {
    if (!fileExpanded) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setFileExpanded(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [fileExpanded]);
  const showAppView = useCallback((id: string) => {
    setComputerOpen(false);
    setFileOpen(null);
    setChangesOpen(null);
    setArtifactOpen(null);
    setArtifactExpanded(false);
    setAppViewOpen(id);
  }, []);
  useEffect(() => onOpenAppView(showAppView), [showAppView]);
  useEffect(() => {
    if (!appViewExpanded) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setAppViewExpanded(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [appViewExpanded]);
  useEffect(() => {
    if (!artifactExpanded) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setArtifactExpanded(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [artifactExpanded]);
  const [desktops, setDesktops] = useState<SandboxDesktop[]>([]);
  useEffect(() => {
    let alive = true;
    sandboxStatus()
      .then((s) => { if (alive) { setSandboxOn(s.enabled); setDesktops(s.desktops || []); } })
      .catch(() => alive && setSandboxOn(false));
    return () => { alive = false; };
  }, []);
  // The chat's side panel (computer preview, artifacts, files...).
  const [sidebarOpen, setSidebarOpen] = useState(() => loadSidebarOpen() === true && isWideScreen());
  const setSidebar = useCallback((open: boolean) => {
    setSidebarOpen(open);
    saveSidebarOpen(open);
  }, []);
  // Chill mode always talks to the main assistant; until it is known, the
  // one it used last.
  const [agentId, setAgentId] = useState<string>(() => (chill ? localStorage.getItem('chillAgentId') || '' : localStorage.getItem('selectedAgentId') || ''));
  const [chillMain, setChillMain] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    if (!chill) return;
    let alive = true;
    api.get<{ main_assistant: { id: string } | null }>('/assistant/main')
      .then((r) => { if (alive) setChillMain(r.data?.main_assistant?.id ?? null); })
      .catch(() => { if (alive) setChillMain(null); });
    return () => { alive = false; };
  }, [chill]);

  // The first-run setup made the user's assistant: list it and switch to it.
  useEffect(() => {
    const onAgentsChanged = (event: Event) => {
      const id = (event as CustomEvent<string>).detail;
      // Switch only once the list has it, or the guard below would fall
      // back to the first agent.
      void refetchAgents({ silent: true }).then(() => {
        if (id) setAgentId(id);
      });
    };
    window.addEventListener(AGENTS_CHANGED_EVENT, onAgentsChanged);
    return () => window.removeEventListener(AGENTS_CHANGED_EVENT, onAgentsChanged);
  }, [refetchAgents]);

  // Conversations — server-side sessions per agent (task runs excluded).
  const convosPath = agentId ? `/sessions/agents/${agentId}?exclude_tasks=true` : null;
  const { data: convos, refetch: refetchConvos, loadedPath: convosLoadedPath } = useResource<ConvoSummary[]>(convosPath);
  // True once `convos` is this agent's list (not the previous agent's).
  const convosReady = convos !== null && convosLoadedPath === convosPath;

  // Every chat, with any agent or team, for the sidebar. Refreshed whenever
  // the current agent's list changes.
  const navigate = useNavigate();
  const { data: teamsData } = useResource<PickerTeam[]>('/teams');
  const teams = useMemo(() => (teamsData || []).filter((t) => t.id), [teamsData]);
  const { data: allChatsData, refetch: refetchAllChats } = useResource<ChatSummary[]>('/sessions/chats');
  // This agent's list moved on (a turn ended, a chat was made): re-read the
  // combined list here and in the app sidebar.
  useEffect(() => {
    if (!convos) return;
    void refetchAllChats({ silent: true });
    chatsStale();
  }, [convos, refetchAllChats]);
  const agentById = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents]);
  const teamById = useMemo(() => new Map(teams.map((t) => [t.id, t])), [teams]);
  const allChats = useMemo(() => {
    const rows = (allChatsData || []).filter((c) =>
      c.kind === 'team' ? !!c.team_id && teamById.has(c.team_id) : !!c.agent_id && agentById.has(c.agent_id),
    );
    // The current agent's list is fresher (it refetches after each turn).
    const fresh = new Map((convos || []).map((c) => [c.id, c]));
    const merged = rows.map((c) => (fresh.has(c.id) ? { ...c, ...fresh.get(c.id)! } : c));
    for (const c of convos || []) {
      if (!merged.some((m) => m.id === c.id)) merged.push({ ...c, kind: 'agent', agent_id: agentId, team_id: null });
    }
    return merged.sort((a, b) =>
      Number(!!b.pinned) - Number(!!a.pinned)
      || String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
  }, [allChatsData, convos, agentById, teamById, agentId]);

  // Who the user chatted with last (agent or team), newest first.
  const recentTargets = useMemo(() => {
    const seen = new Set<string>();
    const out: PickTarget[] = [];
    for (const c of allChats) {
      const target: PickTarget | null = c.kind === 'team'
        ? (c.team_id ? { kind: 'team', id: c.team_id } : null)
        : (c.agent_id ? { kind: 'agent', id: c.agent_id } : null);
      if (!target || seen.has(`${target.kind}:${target.id}`)) continue;
      seen.add(`${target.kind}:${target.id}`);
      out.push(target);
    }
    return out;
  }, [allChats]);
  // Kept false: new chats open on the greeting with an agent picker in the
  // composer rather than a separate who-to-talk-to screen.
  const [picking, setPicking] = useState(false);
  const convosSorted = useMemo(
    () => [...(convos || [])].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')),
    [convos],
  );
  const convosRef = useRef(convosSorted);
  convosRef.current = convosSorted;

  // Conversations still working on the server, including ones the user left.
  const { runs: chatRuns, runningIds, refresh: refreshRuns } = useChatRuns(agentId || null);
  // The conversation whose running turn this page is following from the
  // server's run log (after coming back to it), not from its own stream.
  const [followSid, setFollowSid] = useState<string | null>(null);
  const followSidRef = useRef<string | null>(null);
  followSidRef.current = followSid;
  const runBaseRef = useRef<ChatMessage[]>([]);

  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const activeRef = useRef<string | null>(null);
  activeRef.current = activeSessionId;

  // Approvals and questions from the chat on screen don't also notify.
  useEffect(() => {
    setViewingChat(activeSessionId);
    return () => setViewingChat(null);
  }, [activeSessionId]);

  // ── Incognito ──
  // Picked before a new chat's first message. The chat isn't listed, doesn't
  // use or change memory, and is deleted as soon as the person leaves it.
  const [incognito, setIncognito] = useState(false);
  const incognitoRef = useRef(false);
  incognitoRef.current = incognito;
  const incognitoSidRef = useRef<string | null>(null);
  useEffect(() => {
    const previous = incognitoSidRef.current;
    if (previous && activeSessionId !== previous) {
      incognitoSidRef.current = null;
      setIncognito(false);
      api.delete(`/sessions/${encodeURIComponent(previous)}`).catch(() => { /* swept after a day */ });
      return;
    }
    if (activeSessionId && incognitoRef.current && !previous) {
      incognitoSidRef.current = activeSessionId;
      // Never reopen it after a reload.
      if (agentId) localStorage.setItem(sessionKey(agentId), '');
    }
  }, [activeSessionId, agentId]);
  useEffect(() => () => {
    const left = incognitoSidRef.current;
    if (left) api.delete(`/sessions/${encodeURIComponent(left)}`).catch(() => {});
  }, []);
  // ── Model for this chat ──
  // Picked in the chat box; sent with each message, never saved on the
  // agent. Remembered per chat in this browser.
  const [chatModel, setChatModel] = useState<ChatModel | null>(null);
  const chatModelRef = useRef<ChatModel | null>(null);
  chatModelRef.current = chatModel;
  const modelSidRef = useRef<string | null>(null);
  useEffect(() => {
    const previous = modelSidRef.current;
    modelSidRef.current = activeSessionId;
    if (!activeSessionId) {
      if (previous) setChatModel(null);
      return;
    }
    let saved: ChatModel | null = null;
    try {
      const raw = localStorage.getItem(`chatModel:${activeSessionId}`);
      saved = raw ? JSON.parse(raw) : null;
    } catch { /* not remembered */ }
    if (!previous && !saved && chatModelRef.current) {
      // A new chat's first message: keep what was picked for it.
      try { localStorage.setItem(`chatModel:${activeSessionId}`, JSON.stringify(chatModelRef.current)); } catch { /* not persisted */ }
      return;
    }
    setChatModel(saved && saved.provider && saved.model ? saved : null);
  }, [activeSessionId]);
  useEffect(() => {
    if (!activeRef.current) setChatModel(null);
  }, [agentId]);
  const changeChatModel = (next: ChatModel | null) => {
    setChatModel(next);
    const sid = activeRef.current;
    if (!sid) return;
    try {
      if (next) localStorage.setItem(`chatModel:${sid}`, JSON.stringify(next));
      else localStorage.removeItem(`chatModel:${sid}`);
    } catch { /* not persisted */ }
  };
  // Fast mode for this chat, for agents on a CLI that has one. null follows
  // the agent's own setting; a choice here is remembered per chat.
  const [fastChoice, setFastChoice] = useState<boolean | null>(null);
  const fastChoiceRef = useRef<boolean | null>(null);
  fastChoiceRef.current = fastChoice;
  const fastSidRef = useRef<string | null>(null);
  useEffect(() => {
    const previous = fastSidRef.current;
    fastSidRef.current = activeSessionId;
    if (!activeSessionId) {
      if (previous) setFastChoice(null);
      return;
    }
    let saved: boolean | null = null;
    try {
      const raw = localStorage.getItem(`chatFast:${activeSessionId}`);
      saved = raw === '1' ? true : raw === '0' ? false : null;
    } catch { /* not remembered */ }
    if (!previous && saved === null && fastChoiceRef.current !== null) {
      // A new chat's first message: keep what was picked for it.
      try { localStorage.setItem(`chatFast:${activeSessionId}`, fastChoiceRef.current ? '1' : '0'); } catch { /* not persisted */ }
      return;
    }
    setFastChoice(saved);
  }, [activeSessionId]);
  useEffect(() => {
    if (!activeRef.current) setFastChoice(null);
  }, [agentId]);
  const changeFast = (next: boolean) => {
    setFastChoice(next);
    const sid = activeRef.current;
    if (!sid) return;
    try { localStorage.setItem(`chatFast:${sid}`, next ? '1' : '0'); } catch { /* not persisted */ }
  };
  const waitingRef = useRef(false);
  waitingRef.current = waiting;

  const busy = waiting || streaming || question !== null;
  // Files the agent changed in this chat: the card under the last reply, the
  // side panel's Changes section and the diff viewer share one copy.
  const chatChanges = useChatChanges(activeSessionId, busy);
  const openChanges = useCallback((source?: string, path?: string) => {
    setComputerOpen(false);
    setFileOpen(null);
    setArtifactOpen(null);
    setArtifactExpanded(false);
    setAppViewOpen(null);
    setChangesOpen({ source, path });
  }, []);
  const copyBack = useCallback((src: ChangeSource) => {
    if (src.project) void requestCardAction(copyBackMessage(src.project));
  }, []);
  useEffect(() => { closeChanges(); }, [activeSessionId, closeChanges]);
  useEffect(() => { closeFile(); }, [activeSessionId, closeFile]);
  useEffect(() => {
    if (!changesExpanded) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setChangesExpanded(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [changesExpanded]);
  const chatSwarms = useChatSwarms(incognito ? null : activeSessionId, busy);
  const busyRef = useRef(busy);

  // What this chat made (pages, documents), for its side panel.
  const [chatItems, setChatItems] = useState<ArtifactList | null>(null);
  const [newPageIds, setNewPageIds] = useState<Set<string>>(() => new Set());
  const loadChatItems = useCallback(async (sessionId: string | null) => {
    if (!sessionId) {
      setChatItems(null);
      return;
    }
    try {
      const { data } = await api.get<ArtifactList>('/artifact-pages', { params: { session_id: sessionId } });
      if (activeRef.current !== sessionId || !data) return;
      setChatItems({
        pages: Array.isArray(data.pages) ? data.pages : [],
        images: Array.isArray(data.images) ? data.images : [],
        files: Array.isArray(data.files) ? data.files : [],
        public_links: !!data.public_links,
      });
    } catch {
      // The panel keeps what it had; the chat itself is unaffected.
    }
  }, []);
  useEffect(() => {
    setChatItems(null);
    setNewPageIds(new Set());
    void loadChatItems(activeSessionId);
  }, [activeSessionId, loadChatItems]);
  useEffect(() => {
    if (!busy) void loadChatItems(activeRef.current);
  }, [busy, loadChatItems]);
  // Files the agent made in this chat, for its side panel.
  const [chatFiles, setChatFiles] = useState<ProducedFileDetails[]>([]);
  useEffect(() => {
    const sid = activeSessionId;
    setChatFiles([]);
    if (!sid || busy) return;
    let alive = true;
    listFiles({ session_id: sid }).then((rows) => { if (alive && activeRef.current === sid) setChatFiles(rows.slice(0, 40)); }).catch(() => {});
    return () => { alive = false; };
  }, [activeSessionId, busy]);
  // Connected-app screens this chat opened, for its side panel.
  const [chatAppViews, setChatAppViews] = useState<AppViewSummary[]>([]);
  const [chatAppViewsStale, setChatAppViewsStale] = useState(0);
  useEffect(() => {
    // A screen belongs to its chat.
    setAppViewOpen(null);
    setAppViewExpanded(false);
    setChatAppViews([]);
  }, [activeSessionId]);
  useEffect(() => {
    const sid = activeSessionId;
    if (!sid) return;
    let alive = true;
    listAppViews(sid).then((rows) => { if (alive && activeRef.current === sid) setChatAppViews(Array.isArray(rows) ? rows : []); }).catch(() => {});
    return () => { alive = false; };
  }, [activeSessionId, busy, chatAppViewsStale]);
  useEffect(() => onArtifactUpdate((id) => {
    setNewPageIds((prev) => new Set(prev).add(id));
    void loadChatItems(activeRef.current);
  }), [loadChatItems]);

  // While the panel is open or the agent is working, keep an eye on whether
  // its desktop is running, so the live preview appears and goes by itself.
  useEffect(() => {
    if (!sandboxOn || !(sidebarOpen || busy)) return;
    let alive = true;
    const check = () => {
      if (document.hidden) return;
      sandboxStatus().then((s) => alive && setDesktops(s.desktops || [])).catch(() => {});
    };
    check();
    const timer = window.setInterval(check, 5000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [sandboxOn, sidebarOpen, busy]);
  const agentDesktop = useMemo(
    () => (agentId ? desktops.find((d) => d.running && d.name === desktopName(agentId)) ?? null : null),
    [desktops, agentId],
  );

  // The first time a chat publishes a page or its agent starts the computer,
  // the panel opens by itself (wide screens only). After that it stays how
  // the person left it.
  const hasChatPages = (chatItems?.pages.length ?? 0) > 0;
  useEffect(() => {
    if (chill || loadSidebarOpen() !== null || !isWideScreen()) return;
    if (hasChatPages || agentDesktop) setSidebar(true);
  }, [chill, hasChatPages, agentDesktop, setSidebar]);

  // Ctrl+. (Cmd+. on a Mac) opens and closes the panel.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (chill || event.key !== '.' || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
      event.preventDefault();
      // Same as the header button: with an artifact or the computer showing,
      // the shortcut closes the whole right column.
      if (sidebarOpen || artifactOpen || appViewOpen || computerOpen || changesOpen || fileOpen) {
        closeArtifact();
        closeAppView();
        closeChanges();
        closeFile();
        setComputerOpen(false);
        setSidebar(false);
      } else {
        setSidebar(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [chill, sidebarOpen, artifactOpen, appViewOpen, computerOpen, changesOpen, fileOpen, closeArtifact, closeAppView, closeChanges, closeFile, setSidebar]);
  // Spoken replies: when a turn ends and "read replies aloud" is on, read the
  // answer. Sending a new message stops whatever was being read.
  // Live voice mode reads replies itself and keeps speaking across turns.
  const [liveVoice, setLiveVoice] = useState(false);
  const liveVoiceRef = useRef(liveVoice);
  liveVoiceRef.current = liveVoice;
  const wasBusyRef = useRef(false);
  useEffect(() => {
    const wasBusy = wasBusyRef.current;
    wasBusyRef.current = busy;
    if (liveVoiceRef.current) return;
    if (busy && !wasBusy) stopSpeaking();
    if (!busy && wasBusy && question === null && getSpeechPrefs().autoRead) {
      const last = messagesRef.current[messagesRef.current.length - 1];
      if (last?.role === 'assistant' && last.content.trim()) void speakText(last.id, last.content);
    }
  }, [busy, question]);
  useEffect(() => () => stopSpeaking(), []);
  busyRef.current = busy;
  const turnGenerationRef = useRef(0);
  const stopFallbackRef = useRef<number | null>(null);
  const clearStopFallback = useCallback(() => {
    if (stopFallbackRef.current !== null) {
      window.clearTimeout(stopFallbackRef.current);
      stopFallbackRef.current = null;
    }
  }, []);

  useEffect(() => clearStopFallback, [clearStopFallback]);

  // ── Working folders ──
  // A chat's folders live on its session; a new chat shows the agent's own
  // default folders (none unless set on the agent) and sends any changes with
  // its first message.
  const [savedFolders, setSavedFolders] = useState<WorkFolder[] | null>(null);
  const [draftFolders, setDraftFolders] = useState<WorkFolder[] | null>(null);
  const draftFoldersRef = useRef<WorkFolder[] | null>(null);
  draftFoldersRef.current = draftFolders;
  const agentFolders = useMemo<WorkFolder[]>(
    () => agents.find((a) => a.id === agentId)?.folders ?? [],
    [agents, agentId],
  );
  useEffect(() => {
    // Switching chats or agents drops the new-chat draft. A new chat adopting
    // its id mid-turn keeps it until the saved list loads.
    if (!busyRef.current) setDraftFolders(null);
  }, [activeSessionId, agentId]);
  useEffect(() => {
    setSavedFolders(null);
    if (!activeSessionId || busy) return;
    let cancelled = false;
    getChatFolders(activeSessionId)
      .then((f) => { if (!cancelled) setSavedFolders(f); })
      .catch(() => {});
    return () => { cancelled = true; };
    // Re-read after each turn: an approved folder request changes the list.
  }, [activeSessionId, busy]);
  const shownFolders = savedFolders ?? draftFolders ?? agentFolders;
  const changeFolders = useCallback(async (next: WorkFolder[]) => {
    const sid = activeRef.current;
    if (sid) setSavedFolders(await setChatFolders(sid, next));
    else setDraftFolders(next);
  }, []);

  // ── Run in sandbox ──
  // A chat's shell, files and tests can work in the agents' sandboxed
  // computer. A new chat follows the agent's default until the person flips
  // it, and sends the choice with its first message.
  const [chatSandbox, setChatSandboxState] = useState<ChatSandbox | null>(null);
  const [draftSandbox, setDraftSandbox] = useState<boolean | null>(null);
  const draftSandboxRef = useRef<boolean | null>(null);
  draftSandboxRef.current = draftSandbox;
  useEffect(() => {
    if (!busyRef.current) setDraftSandbox(null);
  }, [activeSessionId, agentId]);
  useEffect(() => {
    setChatSandboxState(null);
    if (!activeSessionId) return;
    let cancelled = false;
    getChatSandbox(activeSessionId)
      .then((v) => { if (!cancelled) setChatSandboxState(v); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [activeSessionId]);
  // Code runs in the sandbox unless the agent is set to use this computer.
  const pickedAgent = agents.find((a) => a.id === agentId);
  const agentSandboxDefault = !!pickedAgent && (!!pickedAgent.run_in_sandbox || (!pickedAgent.code_on_my_computer && sandboxOn));
  const runInSandbox = chatSandbox ? chatSandbox.effective : (draftSandbox ?? agentSandboxDefault);
  const toggleSandbox = useCallback(async () => {
    const sid = activeRef.current;
    const next = !runInSandbox;
    if (sid) {
      try {
        setChatSandboxState(await setChatSandbox(sid, next));
      } catch { /* keep the old state */ }
    } else {
      setDraftSandbox(next);
    }
  }, [runInSandbox]);

  // ── The person's own paired PCs ──
  // A chat can work on one of them instead of its usual computer. A new chat
  // sends the pick with its first message.
  const [pcs, setPcs] = useState<PairedDevice[]>([]);
  const refreshPcs = useCallback(() => { listDevices().then(setPcs).catch(() => {}); }, []);
  useEffect(() => { refreshPcs(); }, [refreshPcs]);
  const [chatComputer, setChatComputerState] = useState<string | null>(null);
  const [draftComputer, setDraftComputer] = useState<string | null>(null);
  const draftComputerRef = useRef<string | null>(null);
  draftComputerRef.current = draftComputer;
  useEffect(() => {
    if (!busyRef.current) setDraftComputer(null);
  }, [activeSessionId]);
  useEffect(() => {
    setChatComputerState(null);
    if (!activeSessionId) return;
    let cancelled = false;
    getChatComputer(activeSessionId)
      .then((v) => { if (!cancelled) setChatComputerState(v.computer); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [activeSessionId]);
  const chatPc = activeSessionId ? chatComputer : draftComputer;
  const chatPcDevice = chatPc ? pcs.find((d) => d.id === chatPc) ?? null : null;
  // Keep the picked PC's online state fresh while a chat works on it.
  useEffect(() => {
    if (!chatPc) return;
    const timer = setInterval(refreshPcs, 20000);
    return () => clearInterval(timer);
  }, [chatPc, refreshPcs]);
  // ── Project ──
  // /home?project=<id> starts a new chat in that project: it takes the
  // project's folders and computer, and the chat box shows the project.
  const [projectsList, setProjectsList] = useState<Project[]>([]);
  useEffect(() => { listProjects().then(setProjectsList).catch(() => {}); }, []);
  const [draftProject, setDraftProject] = useState<string | null>(null);
  const draftProjectRef = useRef<string | null>(null);
  draftProjectRef.current = draftProject;
  const [adoptedProject, setAdoptedProject] = useState<{ sid: string; pid: string } | null>(null);
  useEffect(() => {
    if (!activeSessionId || !draftProjectRef.current) return;
    // A new chat adopting its id keeps the project; opening another chat drops the draft.
    if (busyRef.current) setAdoptedProject({ sid: activeSessionId, pid: draftProjectRef.current });
    setDraftProject(null);
  }, [activeSessionId]);
  useEffect(() => {
    if (activeSessionId || !draftProject) return;
    const p = projectsList.find((x) => x.id === draftProject);
    if (!p) return;
    if (p.folders?.length) setDraftFolders(p.folders);
    if (p.computer === 'sandbox') { setDraftSandbox(true); setDraftComputer(null); }
    else if (p.computer) setDraftComputer(p.computer);
  }, [draftProject, projectsList, agentId, activeSessionId]);
  const chatProjectId = activeSessionId
    ? (allChatsData?.find((c) => c.id === activeSessionId)?.project_id
      ?? (adoptedProject?.sid === activeSessionId ? adoptedProject.pid : null))
    : draftProject;
  const chatProject = chatProjectId ? projectsList.find((p) => p.id === chatProjectId) ?? null : null;

  const changeComputer = useCallback(async (id: string | null) => {
    const sid = activeRef.current;
    if (sid) {
      try {
        setChatComputerState((await setChatComputer(sid, id)).computer);
      } catch { /* keep the old pick */ }
    } else {
      setDraftComputer(id);
    }
  }, []);

  // Default to the first agent once the list loads (or if the saved one is gone).
  useEffect(() => {
    if (agents.length === 0 || chill) return;
    if (!agentId || !agents.some((a) => a.id === agentId)) {
      const first = agents[0].id;
      setAgentId(first);
      localStorage.setItem('selectedAgentId', first);
    }
  }, [agents, agentId, chill]);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (transcript) transcript.scrollTop = transcript.scrollHeight;
  }, [messages, waiting, planning]);

  // Auto-grow the composer to fit multi-line input (capped by max-h-40 → scroll).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const addFiles = useCallback((fileList: FileList | File[]) => {
    const incoming = Array.from(fileList);
    if (incoming.length === 0) return;
    setUploadError(null);
    let total = attachmentsRef.current.reduce((sum, a) => sum + a.file.size, 0);
    const next: Attachment[] = [];
    for (const file of incoming) {
      if (attachmentsRef.current.length + next.length >= MAX_FILES) {
        setUploadError(tr('ui.home.youCanAttachUpTo'));
        break;
      }
      // Pasted/dropped files often arrive with an empty MIME type — derive it
      // from the extension so images still route to vision (not the workspace),
      // and re-tag the file so the thumbnail + downscale actually work.
      const ext = /\.(png|jpe?g|gif|webp|bmp|avif)$/i.exec(file.name)?.[1]?.toLowerCase();
      const imgMime = file.type.startsWith('image/') ? file.type : ext ? `image/${ext === 'jpg' ? 'jpeg' : ext}` : '';
      const isImage = !!imgMime;
      const isDoc = DOC_EXT.test(file.name) || /(pdf|word|excel|spreadsheet|presentation|officedocument|^text\/|rtf|csv|json)/i.test(file.type);
      if (!isImage && !isDoc) {
        setUploadError(tr('ui.home.onlyImagesAndDocumentsCan'));
        continue;
      }
      const bytes = file.size;
      if (bytes > MAX_FILE_BYTES) {
        setUploadError(tr('ui.home.nameIsLargerThan10', { name: file.name }));
        continue;
      }
      if (total + bytes > MAX_TOTAL_BYTES) {
        setUploadError(tr('ui.home.attachmentsExceed25MbTotal'));
        break;
      }
      total += bytes;
      next.push({
        id: crypto.randomUUID(),
        kind: isImage ? 'image' : 'file',
        name: file.name || 'file',
        mime: file.type || imgMime || 'application/octet-stream',
        file,
        previewUrl: isImage && typeof URL.createObjectURL === 'function'
          ? URL.createObjectURL(file)
          : undefined,
      });
    }
    for (const attachment of next) if (attachment.previewUrl) ownedPreviewUrlsRef.current.add(attachment.previewUrl);
    if (next.length) setAttachments((prev) => [...prev, ...next]);
  }, []);

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => {
      const removed = prev.find((attachment) => attachment.id === id);
      revokePreviewUrl(removed?.previewUrl);
      return prev.filter((attachment) => attachment.id !== id);
    });
    setEditSource((current) => (
      current?.type === 'attachment' && current.attachmentId === id ? null : current
    ));
  }, [revokePreviewUrl]);

  const selectArtifactEditSource = useCallback((artifact: ToolArtifact) => {
    setEditSource({ type: 'artifact', artifact });
  }, []);

  const clearComposerMedia = useCallback(() => {
    setAttachments((current) => {
      for (const attachment of current) {
        revokePreviewUrl(attachment.previewUrl);
      }
      return [];
    });
    setEditSource(null);
  }, [revokePreviewUrl]);

  const resetThreadState = useCallback(() => {
    clearStopFallback();
    turnGenerationRef.current += 1;
    clearMessages();
    setWaiting(false);
    setStreaming(false);
    setStopping(false);
    setStopError(null);
    setPlanning(null);
    setApprovals([]);
    setQuestion(null);
    setIsStreaming(false);
    setFollowSid(null);
    setRework(null);
    setReworkError(null);
    setWaitingMsgs([]);
    setSteerError(null);
    resendRef.current = null;
    clearComposerMedia();
  }, [clearComposerMedia, clearMessages, clearStopFallback, setIsStreaming]);

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

  // Chill mode: switch to the main assistant once it is known (or the first
  // agent when there is none), without changing the full app's pick.
  useEffect(() => {
    if (!chill || agents.length === 0 || chillMain === undefined) return;
    const has = (id: string | null | undefined) => !!id && agents.some((a) => a.id === id);
    const want = has(chillMain) ? chillMain! : has(agentId) ? agentId : agents[0].id;
    try { localStorage.setItem('chillAgentId', want); } catch { /* not remembered */ }
    if (want === agentId) return;
    setAgentId(want);
    setActiveSessionId(null);
    resetThreadState();
  }, [chill, agents, chillMain, agentId, resetThreadState]);

  const loadConversation = useCallback(
    async (id: string, expectedGeneration?: number, attach = true) => {
      try {
        const [res, runs] = await Promise.all([
          api.get(`/sessions/${id}/chat`),
          attach && agentId ? fetchChatRuns(agentId).catch(() => []) : Promise.resolve([]),
        ]);
        if (
          expectedGeneration !== undefined
          && turnGenerationRef.current !== expectedGeneration
        ) return false;
        clearComposerMedia();
        adoptSession(id);
        const run = runs.find((item) => item.session_id === id);
        const history = toChatMessages(parseChatEntries(res.data));
        setMessages(history);
        setWaitingMsgs(run?.inbox ?? []);
        resendRef.current = null;
        setStopping(false);
        setStopError(null);
        setPlanning(null);
        setApprovals([]);
        setQuestion(null);
        if (run) {
          // Still working on the server: show what it has done so far and
          // follow it live until it finishes. The turn may already be saved
          // (it ends just after saving), so start from the history before it.
          runBaseRef.current = Array.isArray(res.data) && typeof run.history_len === 'number'
            ? toChatMessages(parseChatEntries(res.data.slice(0, run.history_len)))
            : history;
          setFollowSid(id);
          setWaiting(true);
          setStreaming(false);
          setIsStreaming(true);
        } else {
          setFollowSid(null);
          setWaiting(false);
          setStreaming(false);
          setIsStreaming(false);
          if (attach) {
            // Messages the last turn never read (the page was gone when it
            // ended) go out now as the next turn.
            const pending = await api.get(`/sessions/${encodeURIComponent(id)}/pending`)
              .then((r) => (r.data as { messages?: WaitingMessage[] })?.messages ?? [])
              .catch(() => [] as WaitingMessage[]);
            if (pending.length && activeRef.current === id) {
              resendRef.current = pending.map((m) => m.content).join('\n\n');
              setResendTick((n) => n + 1);
            }
          }
        }
        return true;
      } catch {
        // Deleted/stale id — clear it and let the caller fall back.
        if (
          expectedGeneration !== undefined
          && turnGenerationRef.current !== expectedGeneration
        ) return false;
        if (agentId) localStorage.removeItem(sessionKey(agentId));
        return false;
      }
    },
    [adoptSession, agentId, clearComposerMedia, setIsStreaming, setMessages],
  );

  const finishTurn = useCallback((
    reason: 'complete' | 'stopped' | 'error' = 'complete',
    errorResult?: string,
  ) => {
    clearStopFallback();
    turnGenerationRef.current += 1;
    setStreaming(false);
    setWaiting(false);
    setStopping(false);
    setStopError(null);
    setIsStreaming(false);
    setPlanning(null);
    setApprovals([]);
    setQuestion(null);
    setFollowSid(null);
    if (reason === 'stopped') stopRunningTools();
    else if (reason === 'error') failRunningTools(errorResult);
    if (reason !== 'error') markTurnDuration();
    void refetchConvos({ silent: true });
    void refreshRuns();
  }, [clearStopFallback, failRunningTools, markTurnDuration, refetchConvos, refreshRuns, setIsStreaming, stopRunningTools]);

  // A conversation finished in the background: refresh the list so its
  // new reply time shows.
  const prevRunningRef = useRef<Set<string>>(new Set());
  const runningKey = [...runningIds].sort().join(',');
  useEffect(() => {
    const previous = prevRunningRef.current;
    const current = new Set(runningKey ? runningKey.split(',') : []);
    prevRunningRef.current = current;
    if ([...previous].some((id) => !current.has(id))) void refetchConvos({ silent: true });
  }, [runningKey, refetchConvos]);

  // Restore the last-active conversation when the agent resolves or changes.
  // Waits for the conversation list so the stored id can be validated against
  // it — a stale key could point at a deleted session or a task-run session
  // (which must never open as a chat thread).
  const restoredForAgent = useRef<string | null>(null);
  // Chill mode opened with ?new=1 (from the Home page or another page that
  // isn't a chat): start blank, even after chill settles on its assistant.
  const { search: startSearch } = useLocation();
  const freshStartRef = useRef(chill && new URLSearchParams(startSearch).get('new') === '1');
  useEffect(() => { if (activeSessionId) freshStartRef.current = false; }, [activeSessionId]);
  useEffect(() => {
    if (!agentId || !convosReady) return;
    if (restoredForAgent.current === agentId) return;
    restoredForAgent.current = agentId;
    const stored = localStorage.getItem(sessionKey(agentId));
    if (stored === '' || freshStartRef.current) {
      // Sentinel: the user deliberately left a blank new thread. A fresh
      // chill start writes it too, so a reload keeps the empty chat.
      if (stored !== '') localStorage.setItem(sessionKey(agentId), '');
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
  }, [agentId, convosReady, convosSorted, loadConversation, resetThreadState]);

  // A turn ended with messages it never read: queue them to be sent next.
  const takeUnsent = useCallback((event: object) => {
    const unsent = (event as { unsent?: WaitingMessage[] }).unsent;
    // Messages still held for a brand-new chat stay; they go out below.
    setWaitingMsgs((prev) => prev.filter((m) => m.id.startsWith(HELD_PREFIX)));
    if (Array.isArray(unsent) && unsent.length) {
      resendRef.current = unsent.map((m) => m.content).join('\n\n');
    }
  }, []);

  // When the open chat last heard anything from its turn (text, a tool, a
  // status). A long quiet stretch shows "Still working…" (see below).
  const lastActivityRef = useRef(Date.now());
  const handleSSEEvent = useCallback(
    (event: { type: string; content?: string; action?: string; session_id?: string; tool_name?: string; status?: string; tool_call_id?: string; params?: string; result?: string; artifacts?: ToolArtifact[] }) => {
      const sid = event.session_id;
      lastActivityRef.current = Date.now();
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
        // List the new conversation now, not only once its reply is saved.
        void refetchConvos({ silent: true });
      }
      switch (event.type) {
        case 'generate':
          if (event.content) {
            appendToLastMessage(event.content);
            setWaiting(false);
            setStreaming(true);
            setPlanning(null);
          }
          break;
        case 'files_made': {
          // The files this turn made: under the reply (picked results as cards, the rest on a shelf).
          const made = parseFileRefs((event as { files?: unknown }).files);
          if (made) attachFilesMade(made);
          break;
        }
        case 'turn_complete':
          takeUnsent(event);
          finishTurn();
          notifyReplyRef.current();
          break;
        case 'turn_stopped':
          // Stop keeps what was waiting and sends it next ("send now").
          takeUnsent(event);
          finishTurn('stopped');
          break;
        case 'message_queued': {
          // Sent from elsewhere (another tab, a chat app) while this runs.
          const queued = event as unknown as WaitingMessage;
          if (queued.id && queued.content) {
            setWaitingMsgs((prev) => (prev.some((m) => m.id === queued.id) ? prev : [...prev, queued]));
          }
          break;
        }
        case 'message_withdrawn': {
          const gone = (event as { id?: string }).id;
          if (gone) setWaitingMsgs((prev) => prev.filter((m) => m.id !== gone));
          break;
        }
        case 'message_delivered': {
          // The agent has read these: they join the chat where it read them.
          const read = (event as { messages?: WaitingMessage[] }).messages || [];
          const ids = new Set(read.map((m) => m.id));
          setWaitingMsgs((prev) => prev.filter((m) => !ids.has(m.id)));
          for (const m of read) addMessage('user', m.content, { id: `steer-${m.id}`, steered: true });
          setWaiting(true);
          setStreaming(false);
          break;
        }
        case 'multistep_result':
          if (event.content) appendToLastMessage(event.content);
          finishTurn();
          break;
        case 'status':
          // transient indicator (e.g. "Planning multi-step task…") — NOT a message
          if (event.content) setPlanning(event.content);
          break;
        case 'error':
          takeUnsent(event);
          if (event.content) addMessage('assistant', tr('ui.home.errorContent', { content: event.content }));
          if (event.content?.includes('no longer exists') && agentId) {
            localStorage.removeItem(sessionKey(agentId));
            setActiveSessionId(null);
          }
          finishTurn('error', event.content);
          break;
        case 'tool':
          // The agent invoked a tool — show it running with its params, then flip
          // to done/error and attach the result.
          if (event.tool_name) {
            if (event.status === 'started')
              addToolCall(event.tool_name, { id: event.tool_call_id, params: event.params });
            else {
              resolveToolCall(event.tool_name, event.status === 'error' ? 'error' : 'done', {
                id: event.tool_call_id, result: event.result, artifacts: event.artifacts,
                card: parseCard((event as { outcome?: { card?: unknown } }).outcome?.card) ?? undefined,
                files: parseFileRefs((event as { outcome?: { files?: unknown } }).outcome?.files),
              });
              // A page was published: show it beside the chat (wide screens),
              // and tell an open viewer there is a new version.
              const published = event.status === 'error' ? null : publishedFrom(event.tool_name, event.result);
              if (published) {
                announceArtifactUpdate(published.id);
                // Chill mode keeps it as a preview in the chat until it's clicked.
                if (!chillRef.current && window.matchMedia?.('(min-width: 768px)').matches) showArtifact(published.id);
              }
              // A connected app opened (or changed) its own screen: show it
              // beside the chat, or update the one already showing.
              const appView = event.status === 'error' ? null : appViewFrom(event.result);
              if (appView) {
                announceAppViewUpdate(appView.id);
                setChatAppViewsStale((n) => n + 1);
                if (window.matchMedia?.('(min-width: 768px)').matches) showAppView(appView.id);
              }
            }
          }
          break;
        case 'attachments_ingested': {
          const artifacts = (event.artifacts || [])
            .filter((artifact) => artifact
              && typeof artifact.id === 'string'
              && typeof artifact.mime_type === 'string'
              && /^[A-Za-z0-9_-]{1,128}$/.test(artifact.id)
              && artifact.mime_type.startsWith('image/'))
            .map((artifact) => ({ ...artifact, origin: 'uploaded' as const }));
          attachArtifactsToLatestUser(artifacts);
          break;
        }
        case 'approval_request': {
          const req = event as unknown as ApprovalRequest;
          if (req.request_id) {
            setApprovals((prev) =>
              prev.some((a) => a.request_id === req.request_id) ? prev : [...prev, req],
            );
          }
          break;
        }
        case 'approval_resolved': {
          // Answered here, from a notification, the Inbox or a chat app.
          const rid = (event as { request_id?: string }).request_id;
          if (rid) setApprovals((prev) => prev.filter((a) => a.request_id !== rid));
          break;
        }
        case 'question_request': {
          const req = event as unknown as QuestionRequest;
          if (
            req.request_id
            && req.prompt
            && Array.isArray(req.options)
            && typeof req.allow_free_text === 'boolean'
          ) {
            setQuestion(req);
            setWaiting(false);
            setStreaming(false);
          }
          break;
        }
        default:
          break;
      }
    },
    [appendToLastMessage, attachArtifactsToLatestUser, addMessage, addToolCall, resolveToolCall, attachFilesMade, agentId, finishTurn, refetchConvos, showArtifact, showAppView, takeUnsent],
  );

  // Open the stream once we know which agent to use (or that there are none),
  // so a fresh visitor doesn't connect to the default agent then reconnect.
  const sseReady = !!agentId || (!agentsLoading && agents.length === 0);
  // A reply that finishes while you're in another window raises a system
  // notification (the notification code skips it when you're looking).
  const notifications = useOptionalNotifications();
  const notifyReplyRef = useRef<() => void>(() => undefined);
  notifyReplyRef.current = () => {
    if (!notifications || typeof document === 'undefined') return;
    if (!document.hidden && document.hasFocus()) return;
    const name = agentById.get(agentId)?.name ?? tr('ui.home.theAgent');
    const last = messages[messages.length - 1];
    const text = last?.role === 'assistant' && typeof last.content === 'string' ? last.content.replace(/\s+/g, ' ').trim() : '';
    notifications.push({
      event: 'chat_reply',
      title: tr('ui.home.nameReplied', { name }),
      body: text ? (text.length > 140 ? `${text.slice(0, 139)}…` : text) : tr('ui.home.openChatToReadReply'),
      href: '/home',
    });
  };

  const handleStreamEvent = useCallback((event: SSEEvent) => {
    const sid = typeof event.session_id === 'string' ? event.session_id : undefined;
    if (event.type === 'run_resync') {
      // Part of this turn's output was missed while the page was away:
      // replay it from the server's run log.
      if (sid && sid === activeRef.current) void loadConversation(sid);
      return;
    }
    // That conversation is shown from its run log; skip the duplicate.
    if (sid && sid === followSidRef.current) return;
    handleSSEEvent(event);
  }, [handleSSEEvent, loadConversation]);

  const handleRunEvent = useCallback((event: SSEEvent) => {
    const sid = typeof event.session_id === 'string' ? event.session_id : undefined;
    if (!sid || sid !== followSidRef.current) return;
    if (event.type === 'run_attached') {
      // (Re)start from the saved history plus the message being answered,
      // so a reconnect replays cleanly instead of doubling text.
      const base = [...runBaseRef.current];
      const prompt = typeof event.prompt === 'string' ? event.prompt.trim() : '';
      const last = base[base.length - 1];
      if (prompt && !(last?.role === 'user' && last.content.trim() === prompt)) {
        base.push({
          id: `run-${String(event.run_id ?? sid)}`,
          role: 'user',
          content: prompt,
          timestamp: typeof event.started_at === 'string' ? event.started_at : undefined,
        });
      }
      setMessages(base);
      setWaiting(true);
      setStreaming(false);
      setIsStreaming(true);
      return;
    }
    handleSSEEvent(event);
  }, [handleSSEEvent, setIsStreaming, setMessages]);

  useEventStream<SSEEvent>({
    path: followSid ? `/agents/runs/${encodeURIComponent(followSid)}/events` : null,
    enabled: !!followSid,
    maxRetries: 3,
    onEvent: ({ data }) => handleRunEvent(data),
    onError: useCallback(() => {
      // The run is gone (finished long ago or the server restarted): show
      // the saved conversation instead.
      const sid = followSidRef.current;
      setFollowSid(null);
      if (sid && sid === activeRef.current) void loadConversation(sid, undefined, false);
    }, [loadConversation]),
  });

  // A turn this page didn't send can start in the open chat (an agent-started
  // chat replying back to it): attach to it once the run poll sees it.
  // Each run is attached at most once: a run this page sent or already
  // followed can linger in the poll for a moment after it ends.
  const seenRunsRef = useRef(new Set<string>());
  useEffect(() => {
    const sid = activeSessionId;
    if (!sid) return;
    const fresh = chatRuns.filter((run) => run.session_id === sid && !seenRunsRef.current.has(run.run_id));
    if (fresh.length === 0) return;
    // Already following this chat's run: that is the one listed.
    if (followSid === sid) {
      for (const run of fresh) seenRunsRef.current.add(run.run_id);
      return;
    }
    // Busy with this page's own turn: try again once it ends.
    if (followSid || busy || approvals.length > 0) return;
    for (const run of fresh) seenRunsRef.current.add(run.run_id);
    void loadConversation(sid);
  }, [chatRuns, activeSessionId, followSid, busy, approvals.length, loadConversation]);

  // This page's own turn ended on the server but its last event never got
  // here (the stream dropped or the server restarted mid-turn): the page
  // would show the agent working forever. Once the run poll has seen the
  // turn running and then misses it twice in a row, show the saved chat.
  const ownRunSeenRef = useRef<string | null>(null);
  const ownRunGoneRef = useRef(0);
  useEffect(() => {
    const sid = activeSessionId;
    if (!sid || !busy || followSid) {
      ownRunSeenRef.current = null;
      ownRunGoneRef.current = 0;
      return;
    }
    if (chatRuns.some((run) => run.session_id === sid)) {
      ownRunSeenRef.current = sid;
      ownRunGoneRef.current = 0;
      return;
    }
    if (ownRunSeenRef.current !== sid) return;
    ownRunGoneRef.current += 1;
    if (ownRunGoneRef.current < 2) return;
    ownRunSeenRef.current = null;
    ownRunGoneRef.current = 0;
    finishTurn();
    // Tagged with the new generation, so a turn started before the history
    // arrives isn't overwritten by this load.
    void loadConversation(sid, turnGenerationRef.current);
  }, [chatRuns, activeSessionId, busy, followSid, finishTurn, loadConversation]);

  // Mid-reply silence: the model is thinking without showing it, or writing
  // a long tool call (a whole file for Write). Without a sign of life the
  // chat looks stuck, so after a few quiet seconds show the moving bars.
  const [quiet, setQuiet] = useState(false);
  const lastRow = messages[messages.length - 1];
  const toolRunning = lastRow?.role === 'tool' && !!lastRow.tools?.some((t) => t.status === 'running');
  const canGoQuiet = streaming && !waiting && question === null && approvals.length === 0 && !toolRunning;
  useEffect(() => {
    if (!canGoQuiet) {
      setQuiet(false);
      return;
    }
    const check = () => setQuiet(Date.now() - lastActivityRef.current > QUIET_AFTER_MS);
    check();
    const timer = window.setInterval(check, 1000);
    return () => window.clearInterval(timer);
  }, [canGoQuiet]);

  const { isConnected, isConnecting, error, reconnect: reconnectStream, streamId, rotateStream } = useSSE({
    onMessage: handleStreamEvent,
    onError: useCallback((e: Error) => console.error('SSE error:', e), []),
    agentId: agentId || undefined,
    enabled: sseReady,
  });
  // In the desktop app a lost connection usually means the engine stopped:
  // start it again (the stream reconnects by itself once it's back).
  const engine = notifications?.engine ?? null;
  const reconnect = useCallback(() => {
    if (engine && (engine.status === 'stopped' || engine.status === 'failed')) void desktop.restartEngine();
    reconnectStream();
  }, [engine, reconnectStream]);
  const reconnecting = isConnecting || engine?.status === 'starting';

  type ExecutionMode = 'chat' | 'task';

  // Steering: a message typed while the agent works goes into its current
  // turn and is read after the step it is on.
  const steer = useCallback(async (msg: string, opts: { keepComposer?: boolean } = {}): Promise<boolean> => {
    const sessionId = activeRef.current;
    if (!opts.keepComposer) setInput('');
    setSteerError(null);
    if (!sessionId) {
      // A brand-new chat has no id until its first reply arrives. Hold the
      // message here and send it as soon as the id is known.
      const held = { id: `${HELD_PREFIX}${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, content: msg };
      setWaitingMsgs((prev) => [...prev, held]);
      return true;
    }
    try {
      const res = await api.post('/agents/chat', {
        message: msg,
        execution_mode: 'chat',
        session_id: sessionId,
        ...(agentId ? { agent_id: agentId } : {}),
        ...(streamId ? { stream_id: streamId } : {}),
      });
      const data = res.data as { status?: string; message?: WaitingMessage } | undefined;
      if (data?.status === 'queued' && data.message) {
        const queued = data.message;
        setWaitingMsgs((prev) => (prev.some((m) => m.id === queued.id) ? prev : [...prev, queued]));
      } else {
        // The turn had already ended: this started a new one.
        addMessage('user', msg);
        setWaiting(true);
        setStreaming(false);
        setIsStreaming(true);
      }
      return true;
    } catch (error) {
      if (!opts.keepComposer) setInput((current) => current || msg);
      setSteerError(errorMessage(error, tr('ui.home.couldNotSendThatWhile')));
      return false;
    }
  }, [addMessage, agentId, setIsStreaming, streamId]);

  const send = useCallback(
    async (text: string, executionMode: ExecutionMode = 'chat', replace: Rework | null = rework, opts: { keepComposer?: boolean } = {}): Promise<boolean> => {
      const msg = text.trim();
      const pending = opts.keepComposer ? [] : attachmentsRef.current;
      const pendingEditSource = opts.keepComposer ? null : editSource;
      if (
        busy && question === null && msg && pending.length === 0 && pendingEditSource === null
        && !replace && executionMode === 'chat' && !opts.keepComposer
      ) {
        return steer(msg);
      }
      // A question card is open: words typed in the chat box answer it.
      if (
        question && !question.secret && msg && pending.length === 0 && pendingEditSource === null
        && !replace && executionMode === 'chat' && !opts.keepComposer
      ) {
        setInput('');
        try {
          await postQuestionAction(question, 'answer', { text: msg });
          return true;
        } catch {
          setInput(msg);
          return false;
        }
      }
      if (!isConnected || uploadError || (!msg && pending.length === 0) || busy) return false;
      if (executionMode === 'task' && (pending.length > 0 || pendingEditSource !== null)) return false;
      if (replace) {
        // Take the old message and everything after it out of the saved chat
        // before the new wording goes in.
        if (!activeSessionId) return false;
        try {
          await api.post(`/sessions/${encodeURIComponent(activeSessionId)}/rewind`, {
            turn: replace.turn,
            expected: replace.expected,
            ...(replace.keep ? { keep: true, text: msg } : {}),
          });
        } catch (error) {
          const status = (error as { response?: { status?: number } })?.response?.status;
          setReworkError(status === 409
            ? errorMessage(error, tr('ui.home.thisChatIsStillWorking'))
            : tr('ui.home.thatMessageCouldNotBe'));
          return false;
        }
        if (replace.keep) {
          // The message and its uploads stay; only its wording may change.
          const all = messagesRef.current;
          let end = replace.index + 1;
          while (end < all.length && all[end].role === 'user' && (isUploadNote(all[end]) || !all[end].content.trim())) end += 1;
          const label = /(\n|^)📎 .*$/s.exec(all[replace.index].content)?.[0] ?? '';
          setMessages(all.slice(0, end).map((m, i) => (
            // Re-sent now: the new answer's time is measured from here.
            i === replace.index ? { ...m, timestamp: new Date().toISOString(), content: `${msg}${label && !label.startsWith('\n') && msg ? '\n' : ''}${label}` } : m
          )));
        } else {
          setMessages(messagesRef.current.slice(0, replace.index));
        }
        setRework(null);
        setReworkError(null);
      }
      clearStopFallback();
      turnGenerationRef.current += 1;
      // Keep a record of what was attached in the visible user message.
      const shown = pending.length
        ? `${msg}${msg ? '\n' : ''}📎 ${pending.map((a) => a.name).join(', ')}`
        : msg;
      if (!replace?.keep) addMessage('user', shown);
      if (!opts.keepComposer) {
        setInput('');
        setAttachments([]);
        setEditSource(null);
        setUploadError(null);
      }
      setWaiting(true);
      setStreaming(false);
      setStopping(false);
      setStopError(null);
      setPlanning(null);
      setIsStreaming(true);
      try {
        const editSourceImageIndex = pendingEditSource?.type === 'attachment'
          ? pending.filter((attachment) => attachment.kind === 'image')
            .findIndex((attachment) => attachment.id === pendingEditSource.attachmentId)
          : -1;
        const payload = {
          message: msg,
          execution_mode: executionMode,
          ...(agentId ? { agent_id: agentId } : {}),
          ...(streamId ? { stream_id: streamId } : {}),
          // No id on a fresh thread: the server creates the session and the
          // client adopts its id from the first tagged SSE event.
          ...(activeSessionId ? { session_id: activeSessionId } : {}),
          ...(pendingEditSource?.type === 'artifact'
            ? { edit_source_artifact_id: pendingEditSource.artifact.id }
            : {}),
          ...(editSourceImageIndex >= 0
            ? { edit_source_image_index: editSourceImageIndex }
            : {}),
          ...(bypassRef.current ? { bypass_permissions: true } : {}),
          ...(swarmRef.current && executionMode === 'chat' && !incognitoRef.current ? { swarm: true } : {}),
          ...(reasoningRef.current ? { reasoning_effort: reasoningRef.current } : {}),
          ...(chatModelRef.current ? { model: chatModelRef.current } : {}),
          ...(fastChoiceRef.current !== null ? { fast_mode: fastChoiceRef.current } : {}),
          ...(!activeRef.current && draftFoldersRef.current ? { folders: draftFoldersRef.current } : {}),
          ...(!activeRef.current && incognitoRef.current ? { incognito: true } : {}),
          ...(!activeRef.current && draftSandboxRef.current !== null ? { run_in_sandbox: draftSandboxRef.current } : {}),
          ...(!activeRef.current && draftComputerRef.current ? { computer: draftComputerRef.current } : {}),
          ...(!activeRef.current && draftProjectRef.current ? { project_id: draftProjectRef.current } : {}),
        };
        if (pending.length) {
          const form = new FormData();
          form.append('payload', new Blob([JSON.stringify(payload)], { type: 'application/json' }));
          for (const attachment of pending) form.append('files', attachment.file, attachment.name);
          await api.post('/agents/chat', form);
          for (const attachment of pending) {
            revokePreviewUrl(attachment.previewUrl);
          }
        } else {
          await api.post('/agents/chat', payload);
        }
        window.setTimeout(() => void refreshRuns(), 600);
        return true;
      } catch {
        if (!opts.keepComposer) {
          setInput(text);
          setAttachments(pending);
          setEditSource(pendingEditSource);
        }
        addMessage('assistant', tr('ui.home.unableToReachTheAgent'));
        finishTurn();
        return false;
      }
    },
    [busy, question, postQuestionAction, steer, addMessage, clearStopFallback, editSource, finishTurn, isConnected, setIsStreaming, setMessages, rework, agentId, activeSessionId, streamId, refreshRuns, revokePreviewUrl, uploadError],
  );

  // A button on an agent's card was pressed: tell the agent, as a message.
  // While it is working the press is read between its steps; whatever the
  // person was typing stays in the box.
  useEffect(() => onCardAction((text) => {
    if (!isConnected) return false;
    if (busy) return steer(text, { keepComposer: true });
    return send(text, 'chat', null, { keepComposer: true });
  }), [busy, isConnected, send, steer]);

  // A draft handed over with "send it" (Create app): send once the chat is up.
  const [autoSendOnce] = useState(takeChatAutoSend);
  const autoSendRef = useRef(autoSendOnce);
  useEffect(() => {
    if (!autoSendRef.current || !isConnected || busy || !input.trim()) return;
    autoSendRef.current = false;
    void send(input);
  }, [isConnected, busy, input, send]);

  // Messages the last turn never read go out as the next one, by themselves
  // (whatever is in the box stays there).
  useEffect(() => {
    if (busy || !isConnected || !resendRef.current) return;
    const text = resendRef.current;
    resendRef.current = null;
    void send(text, 'chat', null, { keepComposer: true });
  }, [busy, isConnected, send, resendTick]);

  // Messages held while a new chat waited for its id go out once it has one.
  // If the turn ended without ever getting an id, they go back in the box.
  useEffect(() => {
    const held = waitingMsgs.filter((m) => m.id.startsWith(HELD_PREFIX));
    if (!held.length) return;
    if (activeSessionId) {
      setWaitingMsgs((prev) => prev.filter((m) => !m.id.startsWith(HELD_PREFIX)));
      void (async () => {
        for (const m of held) await steer(m.content);
      })();
    } else if (!busy) {
      setWaitingMsgs((prev) => prev.filter((m) => !m.id.startsWith(HELD_PREFIX)));
      const text = held.map((m) => m.content).join('\n\n');
      setInput((current) => (current.trim() ? `${text}\n${current}` : text));
    }
  }, [activeSessionId, busy, steer, waitingMsgs]);

  const withdrawWaiting = useCallback(async (message: WaitingMessage, edit: boolean) => {
    const sessionId = activeRef.current;
    setSteerError(null);
    if (message.id.startsWith(HELD_PREFIX) || !sessionId) {
      setWaitingMsgs((prev) => prev.filter((m) => m.id !== message.id));
      if (edit) setInput((current) => (current.trim() ? `${message.content}\n${current}` : message.content));
      return;
    }
    try {
      await api.delete(`/agents/runs/${encodeURIComponent(sessionId)}/inbox/${encodeURIComponent(message.id)}`);
      setWaitingMsgs((prev) => prev.filter((m) => m.id !== message.id));
      if (edit) setInput((current) => (current.trim() ? `${message.content}\n${current}` : message.content));
    } catch (error) {
      setSteerError(errorMessage(error, tr('ui.home.theAgentHasAlreadyRead')));
    }
  }, []);

  const stop = useCallback(async () => {
    if (!busy || stopping) return;
    const generation = turnGenerationRef.current;
    setStopping(true);
    setStopError(null);
    clearStopFallback();
    stopFallbackRef.current = window.setTimeout(() => {
      stopFallbackRef.current = null;
      if (turnGenerationRef.current !== generation) return;
      const sessionId = activeRef.current;
      finishTurn('stopped');
      const terminalGeneration = turnGenerationRef.current;
      if (sessionId) void loadConversation(sessionId, terminalGeneration);
    }, STOP_RECONCILE_DELAY_MS);
    try {
      await api.post('/agents/stop', {
        ...(agentId ? { agent_id: agentId } : {}),
        ...(streamId ? { stream_id: streamId } : {}),
        // Stops the conversation's turn even when it runs on a stream this
        // page no longer holds (the user left and came back).
        ...(activeRef.current ? { session_id: activeRef.current } : {}),
      });
      if (turnGenerationRef.current !== generation || !busyRef.current) return;
    } catch (error) {
      if (turnGenerationRef.current !== generation) return;
      if ((error as { response?: { status?: number } })?.response?.status === 409) {
        const sessionId = activeRef.current;
        finishTurn('stopped');
        const terminalGeneration = turnGenerationRef.current;
        if (sessionId) void loadConversation(sessionId, terminalGeneration);
        return;
      }
      clearStopFallback();
      setStopping(false);
      setStopError(tr('ui.home.unableToStopTheResponse'));
    }
  }, [agentId, busy, clearStopFallback, finishTurn, loadConversation, stopping, streamId]);

  // Reword one of your own messages: it goes back into the box, and sending
  // replaces it and every reply after it.
  const startRework = useCallback((message: ChatMessage) => {
    const all = messagesRef.current;
    const index = all.findIndex((m) => m.id === message.id);
    if (index < 0) return;
    setRework({
      turn: all.slice(0, index).filter(isUserTurn).length,
      index,
      expected: message.content,
      keep: carriesUploads(all, index),
    });
    setReworkError(null);
    setInput(message.content.replace(/\n?📎 .*$/s, ''));
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  // Ask the last question again for a different answer.
  const regenerate = useCallback(() => {
    const all = messagesRef.current;
    let index = all.length - 1;
    while (index >= 0 && !isUserTurn(all[index])) index -= 1;
    if (index < 0) return;
    const question = all[index];
    void send(question.content.replace(/\n?📎 .*$/s, ''), 'chat', {
      turn: all.slice(0, index).filter(isUserTurn).length,
      index,
      expected: question.content,
      keep: carriesUploads(all, index),
    });
  }, [send]);

  // Leaving a conversation mid-turn keeps that turn running on the server.
  // This page moves to a fresh browser stream so it can start another turn
  // elsewhere right away.
  const leaveRunningTurn = () => {
    if (!busy) return;
    // Even while following from the run log, this page's own stream may
    // still hold that turn (after a resync), so always move to a fresh one.
    rotateStream();
    void refreshRuns();
    void refetchConvos({ silent: true });
  };

  const switchConversation = (id: string) => {
    setPicking(false);
    if (id === activeSessionId) return;
    if (!incognitoSidRef.current) setIncognito(false);
    leaveRunningTurn();
    resetThreadState();
    void loadConversation(id);
  };

  const newConversation = () => {
    if (!incognitoSidRef.current) setIncognito(false);
    leaveRunningTurn();
    adoptSession(null);
    resetThreadState();
    setInput('');
    inputRef.current?.focus();
  };

  const switchAgent = (id: string) => {
    if (id === agentId) return;
    if (!incognitoSidRef.current) setIncognito(false);
    leaveRunningTurn();
    setAgentId(id);
    localStorage.setItem('selectedAgentId', id);
    setActiveSessionId(null);
    resetThreadState();
    // restore effect picks up this agent's own last conversation
  };

  // Open any chat from the combined list. A chat with another agent switches
  // to that agent first; the restore effect then opens the stored chat.
  const openChat = (chat: ChatSummary) => {
    if (chat.kind === 'team') {
      leaveRunningTurn();
      navigate(chatHref(chat));
      return;
    }
    if (!chat.agent_id || chat.agent_id === agentId) {
      switchConversation(chat.id);
      return;
    }
    setPicking(false);
    localStorage.setItem(sessionKey(chat.agent_id), chat.id);
    switchAgent(chat.agent_id);
  };

  // A swarm worker's chat is not in the chat list; it opens from the Swarm card.
  const openWorkerChat = (chatId: string) => {
    const worker = chatSwarms.swarms.flatMap((s) => s.workers).find((w) => w.chat_id === chatId);
    if (!isWideScreen()) setSidebar(false);
    openChat({ id: chatId, kind: 'agent', agent_id: worker?.agent_id || agentId } as ChatSummary);
  };

  // The address names the chat on screen: /home?chat=<id>. A link, a bookmark
  // or the back button opens that chat; opening a chat here updates the address.
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedChat = searchParams.get('chat');
  // The chat id this page last wrote to (or read from) the address.
  const addressChatRef = useRef<string | null>(null);
  // A chat id in the address that has not been opened yet (first load, back button).
  const pendingLinkRef = useRef<string | null>(linkedChat);
  if (linkedChat !== addressChatRef.current && linkedChat) pendingLinkRef.current = linkedChat;
  useEffect(() => {
    if (!linkedChat || linkedChat === addressChatRef.current || !allChatsData) return;
    const chat = allChatsData.find((c) => c.id === linkedChat);
    addressChatRef.current = linkedChat;
    pendingLinkRef.current = null;
    if (chat) {
      if (chat.id !== activeRef.current) openChat(chat);
    } else {
      // A link to a chat that is gone: drop it.
      addressChatRef.current = null;
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.delete('chat');
        return next;
      }, { replace: true });
    }
    // openChat changes every render; this runs once per link.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkedChat, allChatsData]);
  const shownChatId = incognito ? null : activeSessionId;
  useEffect(() => {
    // Wait a moment: switching agents passes through "no chat" on the way.
    const timer = window.setTimeout(() => {
      if (pendingLinkRef.current) return;
      const inAddress = new URLSearchParams(window.location.search).get('chat');
      if (shownChatId === inAddress) { addressChatRef.current = inAddress; return; }
      addressChatRef.current = shownChatId;
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (shownChatId) next.set('chat', shownChatId);
        else next.delete('chat');
        return next;
        // Chat to chat is a step back can undo; a new chat getting its id is not.
      }, { replace: !inAddress });
    }, 200);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownChatId]);

  // Start a fresh chat with an agent, or open a team's group chat.
  const startChatWith = (target: PickTarget) => {
    setPicking(false);
    if (target.kind === 'team') {
      leaveRunningTurn();
      navigate(newChatHref(target));
      return;
    }
    if (target.id === agentId) {
      newConversation();
      return;
    }
    // Empty-string sentinel: open a blank thread, not this agent's last chat.
    localStorage.setItem(sessionKey(target.id), '');
    switchAgent(target.id);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  // /home?new=1 (the sidebar's New chat) or ?new=agent:<id>: a fresh chat.
  const linkedNew = searchParams.get('new');
  useEffect(() => {
    if (!linkedNew) return;
    const [kind, id] = linkedNew.split(':');
    // Keep an agent link until the agent list has loaded, so it isn't lost.
    if (kind === 'agent' && id && agentsLoading && !agents.some((a) => a.id === id)) return;
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('new');
      return next;
    }, { replace: true });
    if (kind === 'agent' && id && agents.some((a) => a.id === id)) startChatWith({ kind: 'agent', id });
    else newConversation();
    // startChatWith and newConversation change every render; this runs once per link.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkedNew, agents.length, agentsLoading]);

  // /home?project=<id> (a project's New chat): after ?new= is handled.
  const linkedProject = searchParams.get('project');
  useEffect(() => {
    if (!linkedProject || linkedNew) return;
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('project');
      return next;
    }, { replace: true });
    getProject(linkedProject).then((p) => {
      setProjectsList((prev) => [...prev.filter((x) => x.id !== p.id), p]);
      setDraftProject(p.id);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkedProject, linkedNew]);

  // Tell the app sidebar which chat is open and which are running.
  const runningList = useMemo(() => (runningKey ? runningKey.split(',') : []), [runningKey]);
  useEffect(() => {
    setChatNav({ activeChatId: incognito ? null : activeSessionId, runningIds: runningList });
  }, [activeSessionId, runningList, incognito]);
  useEffect(() => () => setChatNav({ activeChatId: null, runningIds: [] }), []);

  // The sidebar renamed, pinned, archived or deleted a chat: re-read the lists,
  // and leave the chat on screen if it was the one deleted.
  const leaveDeletedRef = useRef<(id: string) => void>(() => {});
  leaveDeletedRef.current = (id: string) => {
    if (id === activeRef.current) newConversation();
  };
  useEffect(() => {
    const onEdited = (event: Event) => {
      void refetchConvos({ silent: true });
      void refetchAllChats({ silent: true });
      const deleted = (event as CustomEvent<ChatsEditedDetail>).detail?.deletedId;
      if (deleted) leaveDeletedRef.current(deleted);
    };
    window.addEventListener(CHATS_EDITED, onEdited);
    return () => window.removeEventListener(CHATS_EDITED, onEdited);
  }, [refetchConvos, refetchAllChats]);

  const empty = messages.length === 0;
  // Saved one-to-one chats only, and never while a reply is on its way.
  const canRework = !!activeSessionId && !busy && isConnected && !incognito;
  let lastQuestionIndex = messages.length - 1;
  while (lastQuestionIndex >= 0 && !isUserTurn(messages[lastQuestionIndex])) lastQuestionIndex -= 1;
  const canRegenerate = lastQuestionIndex >= 0;
  // Ref so the handler stays the same object and finished replies don't re-render.
  const sendRef = useRef(send);
  sendRef.current = send;
  const acceptNextStep = useCallback((step: string) => {
    void sendRef.current(tr('ui.home.yesGoAheadStep', { step }), 'chat', null, { keepComposer: true });
  }, []);
  // A page published twice in one turn shows one card: the newest version's.
  const supersededCards = useMemo(() => {
    const hidden = new Map<string, string>();
    let latest = new Map<string, { messageId: string; toolId: string }>();
    for (const message of messages) {
      if (message.role === 'user') { latest = new Map(); continue; }
      (message.tools ?? []).forEach((tool, index) => {
        const published = tool.status === 'done' ? publishedFrom(tool.name, tool.result ?? undefined) : null;
        if (!published) return;
        const before = latest.get(published.id);
        if (before) hidden.set(before.messageId, [hidden.get(before.messageId), before.toolId].filter(Boolean).join(','));
        latest.set(published.id, { messageId: message.id, toolId: tool.id || String(index) });
      });
    }
    return hidden;
  }, [messages]);
  let lastReplyIndex = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'assistant' && messages[i].content.trim()) {
      lastReplyIndex = i;
      break;
    }
    if (isUserTurn(messages[i])) break;
  }

  // ── The chat's side panel ──
  const sidebarShown = sidebarOpen || !!artifactOpen || !!appViewOpen || computerOpen || !!changesOpen || !!fileOpen;
  const closeRightSide = () => {
    closeFile();
    closeArtifact();
    closeAppView();
    closeChanges();
    setComputerOpen(false);
    setSidebar(false);
  };
  const sidebarWaiting: SidebarWaiting[] = [
    ...approvals.map((a): SidebarWaiting => ({ id: a.request_id, kind: 'approval', title: tr('ui.home.runToolName', { tool_name: a.tool_name }), detail: a.details || undefined })),
    ...(question ? [{ id: question.request_id, kind: 'question' as const, title: question.prompt, detail: question.details || undefined }] : []),
  ];
  // Images in this chat (sent or made), newest first.
  const chatImages = useMemo(() => {
    const seen = new Set<string>();
    const out: ToolArtifact[] = [];
    for (let i = messages.length - 1; i >= 0 && out.length < 12; i -= 1) {
      const message = messages[i];
      for (const artifact of [...(message.artifacts || []), ...(message.tools || []).flatMap((t) => t.artifacts || [])]) {
        if (!artifact.mime_type.startsWith('image/') || seen.has(artifact.id)) continue;
        seen.add(artifact.id);
        out.push(artifact);
      }
    }
    return out;
  }, [messages]);
  // What the agent did since the last thing the person said.
  const chatActivity = useMemo(() => {
    let from = messages.length;
    while (from > 0 && messages[from - 1].role !== 'user') from -= 1;
    const steps: SidebarActivity[] = [];
    for (const message of messages.slice(from)) {
      (message.tools || []).forEach((tool, i) => steps.push({ key: `${message.id}:${tool.id || i}`, name: tool.name.replace(/_/g, ' '), status: tool.status }));
    }
    return steps.slice(-12);
  }, [messages]);
  const pageCount = chatItems?.pages.length ?? 0;
  const sidebarSummary = [
    agentDesktop ? 'Computer' : '',
    pageCount ? `${pageCount} ${pageCount === 1 ? 'artifact' : 'artifacts'}` : '',
  ].filter(Boolean).join(' · ');
  const activeChat = allChats.find((c) => c.id === activeSessionId);
  const currentAgent = agentById.get(agentId);
  // Fast mode is offered when this chat runs on a CLI that has one.
  const fastProvider = hasFastMode(chatModel?.provider || currentAgent?.llm?.provider);
  const fastOn = fastProvider && (fastChoice ?? currentAgent?.llm?.fast_mode === true);
  const chatStarted = parseStamp(activeChat?.datetime || activeChat?.updated_at)?.toLocaleString(dateLocale(), { dateStyle: 'medium', timeStyle: 'short' });


  // The message box: in the middle of a new chat's greeting, else at the bottom.
  const lastReply = useMemo(() => {
    const last = messages[messages.length - 1];
    return last?.role === 'assistant' ? { id: last.id, content: last.content } : null;
  }, [messages]);

  // Side questions: asked beside the chat without interrupting it (Ctrl+/).
  const [sideOpen, setSideOpen] = useState(false);
  useEffect(() => {
    if (!activeSessionId) setSideOpen(false);
  }, [activeSessionId]);
  useEffect(() => {
    if (!activeSessionId) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === '/') {
        e.preventDefault();
        setSideOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeSessionId]);

  const live = useLiveVoice({
    enabled: liveVoice,
    busy,
    approvals,
    question,
    agentName: currentAgent?.name || '',
    lastReply,
    onSend: (text) => (busy ? steer(text, { keepComposer: true }) : send(text, 'chat', null, { keepComposer: true })),
    onStop: () => void stop(),
    onApproval: (id, approved) => void respondApproval(id, approved),
    onAnswer: (q, answer) => {
      const current = question;
      if (current && current.request_id === q.request_id) void postQuestionAction(current, 'answer', answer).catch(() => undefined);
    },
    onExit: () => setLiveVoice(false),
  });

  const composer = (
    <div className="relative mx-auto max-w-3xl">
      {sideOpen && activeSessionId && (
        <div className="animate-rise absolute bottom-full left-0 right-0 z-20 mb-2 md:left-auto md:w-[26rem]">
          <SideQuestionsPanel
            sessionId={activeSessionId}
            busy={busy}
            agentName={currentAgent?.name || ''}
            onClose={() => { setSideOpen(false); inputRef.current?.focus(); }}
            onUseInChat={(text) => { setInput(text); setSideOpen(false); inputRef.current?.focus(); }}
          />
        </div>
      )}
      {skillMenuOpen && (
        <div
          id="skill-menu"
          role="listbox"
          className="animate-rise absolute bottom-full left-0 right-0 z-20 mb-2 overflow-hidden rounded-md border border-line bg-panel shadow-lg"
        >
          <div className="border-b border-line px-3 py-1.5 font-mono text-[10px] tracking-[0.18em] text-fg-dim">{tr('ui.home.skills')}</div>
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
          'relative rounded-2xl border bg-panel shadow-[0_6px_24px_rgba(0,0,0,0.06)] transition-colors dark:shadow-[0_6px_24px_rgba(0,0,0,0.35)]',
          // subtle 1px accent border on focus/drag — no glow ring
          dragOver ? 'border-accent' : 'border-line focus-within:border-fg-dim',
        )}
      >
        {editSource?.type === 'artifact' && (
          <div className="flex items-center gap-2 border-b border-line bg-panel-2 px-3 py-2 font-mono text-[10.5px]">
            <div
              data-testid="edit-source-thumbnail"
              aria-hidden="true"
              className="grid h-8 w-8 flex-none place-items-center overflow-hidden rounded-sm border border-line bg-panel text-accent-ink"
            >
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8" cy="9" r="1.5" /><path d="m4 18 5-5 3 3 3-3 5 5" /></svg>
            </div>
            <div className="min-w-0 flex-1">
              <span className="block tracking-[0.12em] text-accent-ink">{tr('ui.home.editingImage')}</span>
              <span className="block truncate text-fg" title={editSource.artifact.filename || editSource.artifact.id}>
                {editSource.artifact.filename || tr('ui.home.imageSlice', { slice: editSource.artifact.id.slice(0, 8) })}
              </span>
            </div>
            <button
              type="button"
              aria-label={tr('ui.home.removeFilenameEditSource', { filename: editSource.artifact.filename || `image-${editSource.artifact.id.slice(0, 8)}` })}
              className="flex min-h-11 min-w-11 h-11 w-11 flex-none items-center justify-center text-fg-dim transition-colors hover:text-danger-ink md:h-8 md:w-8"
              onClick={() => setEditSource(null)}
            >
              <span aria-hidden="true">×</span>
            </button>
          </div>
        )}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 border-b border-line px-3 pb-1 pt-2.5">
            {attachments.map((a) => (
              <span
                key={a.id}
                className="group inline-flex max-w-full items-center gap-1.5 rounded border border-line bg-panel-2 py-1 pl-1 pr-1.5 font-mono text-[10.5px]"
              >
                {a.kind === 'image' ? (
                  <button
                    type="button"
                    aria-label={tr('ui.home.useNameAsEditSource', { name: a.name })}
                    aria-pressed={editSource?.type === 'attachment' && editSource.attachmentId === a.id}
                    title={tr('ui.home.useAsEditSource')}
                    className={cn(
                      'relative h-8 w-8 flex-none overflow-hidden rounded-sm border transition-colors',
                      editSource?.type === 'attachment' && editSource.attachmentId === a.id
                        ? 'border-accent ring-1 ring-accent'
                        : 'border-transparent hover:border-fg-dim',
                    )}
                    onClick={() => setEditSource({ type: 'attachment', attachmentId: a.id })}
                  >
                    {a.previewUrl
                      ? <img src={a.previewUrl} alt="" className="h-full w-full object-cover" />
                      : <span aria-hidden className="text-accent-ink">â—‡</span>}
                  </button>
                ) : (
                  <svg className="h-4 w-4 flex-none text-fg-dim" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>
                )}
                <span className="truncate text-fg" title={a.name}>{a.name}</span>
                <button
                  onClick={() => removeAttachment(a.id)}
                  aria-label={tr('ui.home.removeName', { name: a.name })}
                  className="grid h-11 w-11 flex-none place-items-center text-fg-dim transition-colors hover:text-danger-ink md:h-auto md:w-auto"
                >
                  ✕
                </button>
              </span>
            ))}
            {editSource?.type === 'attachment' && (
              <button
                type="button"
                aria-label={tr('ui.home.clearEditSource')}
                className="min-h-11 px-1 font-mono text-[10.5px] text-fg-dim transition-colors hover:text-danger-ink md:min-h-0"
                onClick={() => setEditSource(null)}
              >
                {tr('ui.home.clearEdit')}
              </button>
            )}
          </div>
        )}
        {voice.active && <VoiceBar voice={voice} />}
        {liveVoice && (
          <div className="border-b border-line">
            <LiveVoicePanel live={live} approvals={approvals} question={question} onExit={() => setLiveVoice(false)} />
          </div>
        )}
        {(rework || reworkError) && (
          <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-1.5 text-[12px] text-fg-dim" role="status">
            <span className={reworkError ? 'text-danger-ink' : undefined}>
              {reworkError || tr('ui.home.editingAnEarlierMessageSending')}
            </span>
            <button
              type="button"
              className="min-h-11 flex-none font-mono text-[10.5px] hover:text-fg md:min-h-0"
              onClick={() => { setRework(null); setReworkError(null); setInput(''); }}
            >
              {tr('ui.home.cancel')}
            </button>
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
            if (e.key === 'Escape' && busy && !stopping) {
              // Stop the current step; anything waiting goes next.
              e.preventDefault();
              void stop();
              return;
            }
            if (isSendKey(e, getSendKey())) {
              e.preventDefault();
              send(input);
            }
          }}
          role="combobox"
          aria-label={tr('ui.home.messageTheAgent')}
          aria-expanded={skillMenuOpen}
          aria-controls="skill-menu"
          aria-activedescendant={skillMenuOpen ? `skill-opt-${skillIndex}` : undefined}
          placeholder={question && !question.secret
            ? tr('ui.home.answerAboveOrTypeHere')
            : busy && question === null
            ? tr('ui.home.addToWhatItS')
            : empty ? tr('ui.home.askAnything') : tr('ui.home.replyToName', { name: currentAgent?.name || 'the agent' })}
          className={cn('block min-h-11 max-h-48 w-full resize-none bg-transparent outline-none focus-visible:outline-none px-4 pb-1 pt-3.5 text-[15px] text-fg placeholder:text-fg-dim', voice.active && '!hidden')}
        />
        <div className={cn('flex min-w-0 items-center gap-0.5 px-2 pb-2 pt-1', voice.active && '!hidden')}>
          <div className="min-w-0">
          <ComposerMenu
            onAttach={() => fileInputRef.current?.click()}
            onRunTask={() => void send(input, 'task')}
            runTaskDisabled={busy || !!uploadError || !isConnected || !input.trim() || attachments.length > 0 || editSource !== null}
            runTaskTitle={attachments.length > 0 || editSource !== null
              ? tr('ui.home.taskModeDoesNotSupport')
              : tr('ui.home.createAPersistedTaskAnd')}
            bypass={bypass}
            onToggleBypass={toggleBypass}
            incognito={incognito}
            onToggleIncognito={() => setIncognito((v) => !v)}
            incognitoDisabled={!!activeSessionId}
            incognitoTitle={activeSessionId
              ? (incognito ? tr('ui.home.thisChatIsIncognito') : tr('ui.home.incognitoCanOnlyBeTurned'))
              : tr('ui.home.incognitoTheChatIsnT')}
            reasoning={reasoning}
            onReasoning={changeReasoning}
            swarm={incognito ? undefined : { on: swarmOn, onToggle: () => setSwarmOn((v) => !v) }}
            fast={fastProvider ? { on: fastOn, onToggle: () => changeFast(!fastOn) } : undefined}
            sandbox={!chatPc && (sandboxOn || runInSandbox) ? {
              on: runInSandbox,
              available: sandboxOn,
              onToggle: () => void toggleSandbox(),
              hideTag: pcs.length > 0,
            } : undefined}
            phone={isPhone}
            onLiveVoice={() => setLiveVoice(true)}
            liveVoiceDisabled={!isConnected}
            model={<ChatModelPicker agentLlm={currentAgent?.llm} value={chatModel} onChange={changeChatModel} />}
            settings={(
              <>
                {(pcs.length > 0 || chatPc) && (
                  <ComputerChip
                    devices={pcs}
                    value={chatPc}
                    onChange={(id) => void changeComputer(id)}
                    usualIsSandbox={runInSandbox}
                    onPair={() => navigate('/settings/computers')}
                    onOpen={refreshPcs}
                    disabled={busy}
                  />
                )}
                {!chatPc && (
                  <FolderChips
                    variant="button"
                    className="min-w-0"
                    folders={shownFolders}
                    onChange={changeFolders}
                    emptyLabel={tr('ui.home.scratchFolder')}
                    disabled={busy}
                  />
                )}
              </>
            )}
          >
            {empty && (
              <div className="min-w-0 flex-none">
                <AgentPickerChip agents={agents} teams={teams} currentAgentId={agentId} onPick={startChatWith} />
              </div>
            )}
            {chatProject && (
              <span className="inline-flex h-9 min-w-0 max-w-[180px] flex-none items-center gap-1.5 rounded-lg border border-line px-2 text-[12px] text-fg-dim md:h-7"
                title={tr('ui.projects.inProject', { name: chatProject.name })}>
                <span className="h-2 w-2 flex-none rounded-full" style={{ background: projectSwatch(chatProject) }} />
                <button type="button" onClick={() => navigate(`/projects/${chatProject.id}`)} className="min-w-0 truncate hover:text-fg">{chatProject.name}</button>
                {!activeSessionId && (
                  <button type="button" aria-label={tr('ui.projects.notInProject')} onClick={() => setDraftProject(null)} className="flex-none hover:text-fg">✕</button>
                )}
              </span>
            )}
          </ComposerMenu>
          </div>
          <span className="flex-1" />
          {!voice.active && !liveVoice && !(busy && input.trim()) && (
            <>
              {activeSessionId && (
                <SideQuestionButton onClick={() => setSideOpen((v) => !v)} active={sideOpen} className="rounded-lg max-sm:hidden" />
              )}
              {!isPhone && <LiveVoiceButton onClick={() => setLiveVoice(true)} disabled={!isConnected} className="rounded-lg" />}
              <MicButton voice={voice} className="rounded-lg" />
            </>
          )}
          {busy && !!input.trim() && !voice.active && (
            // While it works, typed text is sent into the running task;
            // Stop moves over to where the mic was.
            <button
              onClick={() => void stop()}
              disabled={stopping}
              aria-label={stopping ? tr('ui.home.stoppingResponse') : tr('ui.home.stopResponse')}
              className="grid h-11 w-11 place-items-center rounded-full border border-line text-fg transition hover:border-danger hover:text-danger-ink disabled:opacity-60 md:h-9 md:w-9"
            >
              <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1" /></svg>
            </button>
          )}
          <button
            onClick={() => { if (busy && !input.trim()) void stop(); else void send(input); }}
            disabled={busy && !input.trim() ? stopping : (!!uploadError || !isConnected || (!input.trim() && attachments.length === 0))}
            aria-label={busy && !input.trim() ? (stopping ? tr('ui.home.stoppingResponse') : tr('ui.home.stopResponse')) : tr('ui.home.send')}
            className={cn(
              'grid h-11 w-11 flex-none place-items-center rounded-full transition disabled:cursor-not-allowed disabled:opacity-50 hover:brightness-105 md:h-8 md:w-8',
              busy && !input.trim() ? 'bg-danger text-bg' : 'bg-accent text-accent-foreground',
              voice.active && '!hidden',
            )}
          >
            {busy && !input.trim() && stopping ? (
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" className="opacity-25" /><path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" /></svg>
            ) : busy && !input.trim() ? (
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
          onChange={(e) => { void addFiles(e.target.files || []); e.target.value = ''; }}
        />
      </div>
      {uploadError && (
        <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-danger-ink">{uploadError}</div>
      )}
      {stopError && (
        <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-danger-ink">{stopError}</div>
      )}
      {steerError && (
        <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-danger-ink">{steerError}</div>
      )}
      <VoiceNotice voice={voice} />
      {/* Key hints: only before the first message, and never over the theme's drawing. */}
      {empty && (
        <div className="mt-1.5 hidden items-center gap-x-4 px-2 text-[11px] text-fg-dim md:flex">
          <SendKeyHint />
        </div>
      )}
    </div>
  );

  // ── Chill mode ──
  // What the chat made shows as previews; clicking one fills the window.
  const closeStage = useCallback(() => {
    closeArtifact();
    closeAppView();
    closeChanges();
    closeFile();
    setComputerOpen(false);
  }, [closeArtifact, closeAppView, closeChanges, closeFile]);
  const showComputer = useCallback(() => {
    closeArtifact();
    closeAppView();
    closeChanges();
    closeFile();
    setComputerOpen(true);
  }, [closeArtifact, closeAppView, closeChanges, closeFile]);
  // Turn by turn: null is the newest turn, which also follows new turns.
  const [chillTurnPick, setChillTurnPick] = useState<number | null>(null);
  const chillWasBusyRef = useRef(false);
  useEffect(() => {
    const was = chillWasBusyRef.current;
    chillWasBusyRef.current = busy;
    // A new turn: the answer comes back on screen while it is written.
    if (chill && busy && !was) { closeStage(); setChillTurnPick(null); }
  }, [chill, busy, closeStage]);

  useEffect(() => { setChillTurnPick(null); }, [activeSessionId]);
  const chillTurnRanges = useMemo(() => (chill ? splitTurns(messages, isUserTurn) : []), [chill, messages]);
  const chillLastTurn = chillTurnRanges.length - 1;
  const chillTurnIndex = chillTurnPick !== null && chillTurnPick < chillLastTurn ? chillTurnPick : Math.max(0, chillLastTurn);
  const chillOnLatest = chillTurnIndex >= chillLastTurn;
  /** The assistant's part of the turn on screen. */
  const chillReplies = useMemo(() => repliesOf(messages, chillTurnRanges[chillTurnIndex]), [messages, chillTurnRanges, chillTurnIndex]);
  const chillTurns = useMemo((): ChillTurn[] => chillTurnRanges.map((range) => {
    const asked = range.user === null ? null : messages[range.user];
    const made = madeInTurn(repliesOf(messages, range));
    const first = made.pages[0] ? { made: 'page' as const, madeName: made.pages[0].title }
      : made.files[0] ? { made: 'file' as const, madeName: made.files[0].name } : { made: null, madeName: null };
    return {
      key: asked?.id ?? `start:${range.start}`,
      question: asked ? asked.content.replace(/\n?📎 .*$/s, '') : '',
      at: asked?.timestamp ?? messages[range.start]?.timestamp ?? null,
      ...first,
    };
  }), [chillTurnRanges, messages]);
  const pickChillTurn = useCallback((index: number) => {
    closeStage();
    setChillTurnPick(index >= chillLastTurn ? null : index);
  }, [closeStage, chillLastTurn]);

  const chillRail = useMemo((): ChillRailItem[] => {
    if (!chill) return [];
    const items: ChillRailItem[] = [{ key: 'answer', kind: 'answer', label: tr('ui.chill.answer') }];
    for (const page of chatItems?.pages ?? []) items.push({ key: `page:${page.id}`, kind: 'page', label: page.title });
    for (const file of chatFiles) items.push({ key: `file:${file.id}`, kind: 'file', label: file.name, fileKind: file.kind });
    for (const view of chatAppViews) items.push({ key: `app:${view.id}`, kind: 'app', label: view.title || view.app });
    if (agentDesktop || chatPcDevice) items.push({ key: 'computer', kind: 'computer', label: chatPcDevice ? chatPcDevice.name : tr('ui.chill.assistantSComputer') });
    // Something on screen that the lists haven't caught up with yet.
    const open = artifactOpen ? `page:${artifactOpen}` : fileOpen ? `file:${fileOpen}` : appViewOpen ? `app:${appViewOpen}` : null;
    if (open && !items.some((i) => i.key === open)) {
      items.push(open.startsWith('page') ? { key: open, kind: 'page', label: tr('ui.chill.page') }
        : open.startsWith('file') ? { key: open, kind: 'file', label: tr('ui.chill.file') }
          : { key: open, kind: 'app', label: tr('ui.chill.app') });
    }
    if (computerOpen && !items.some((i) => i.key === 'computer')) items.push({ key: 'computer', kind: 'computer', label: tr('ui.chill.assistantSComputer') });
    return items;
  }, [chill, chatItems, chatFiles, chatAppViews, agentDesktop, chatPcDevice, artifactOpen, fileOpen, appViewOpen, computerOpen]);
  // Pages and files show as previews in their own turn; apps and the computer
  // have none, so they get a strip over the stage.
  const chillStripItems = useMemo(() => {
    const others = chillRail.filter((item) => item.kind === 'app' || item.kind === 'computer');
    return others.length ? [chillRail[0], ...others] : [];
  }, [chillRail]);
  const chillActive = artifactOpen ? `page:${artifactOpen}` : fileOpen ? `file:${fileOpen}` : appViewOpen ? `app:${appViewOpen}`
    : changesOpen ? 'changes' : computerOpen ? 'computer' : 'answer';
  const openChillItem = (key: string) => {
    if (key === 'answer') closeStage();
    else if (key === 'computer') showComputer();
    else if (key.startsWith('page:')) showArtifact(key.slice(5));
    else if (key.startsWith('file:')) showFile(key.slice(5));
    else if (key.startsWith('app:')) showAppView(key.slice(4));
  };

  // The helper agent the assistant is working with right now.
  const chillHelper = useMemo(() => {
    if (!chill || !busy) return null;
    for (let i = messages.length - 1; i >= 0 && !isUserTurn(messages[i]); i -= 1) {
      const tools = messages[i].tools ?? [];
      for (let j = tools.length - 1; j >= 0; j -= 1) {
        const tool = tools[j];
        if (tool.status !== 'running') continue;
        const name = tool.name.toLowerCase().replace(/[\s-]+/g, '_');
        if (!['call_agent', 'start_chat', 'create_agent', 'message_chat', 'check_chat', 'create_new_team'].includes(name)) continue;
        let params: Record<string, unknown> = {};
        try { params = JSON.parse(tool.params || '{}'); } catch { /* shown without a name */ }
        const who = String(params.name || params.agent || params.agent_name || '').trim();
        const doing = name === 'create_agent' ? tr('ui.chill.settingUpANewHelper')
          : name === 'call_agent' ? tr('ui.chill.workingOnAPart')
            : name === 'start_chat' ? tr('ui.chill.startedOnItsPart')
              : name === 'create_new_team' ? tr('ui.chill.puttingATeamTogether')
                : tr('ui.chill.checkingIn');
        return { name: who || tr('ui.chill.helper'), doing };
      }
    }
    return null;
  }, [chill, busy, messages]);

  // What the assistant is doing right now, shown while it works.
  const chillDoing = useMemo(() => {
    if (!chill || !busy) return null;
    for (let i = messages.length - 1; i >= 0 && !isUserTurn(messages[i]); i -= 1) {
      const running = (messages[i].tools ?? []).filter((t) => t.status === 'running');
      if (running.length) return humanizeToolName(running[running.length - 1].name);
    }
    return null;
  }, [chill, busy, messages]);

  const chillQuestion = chillTurns[chillTurnIndex]?.question || null;
  const chillAnswer = useMemo(() => {
    for (let i = chillReplies.length - 1; i >= 0; i -= 1) {
      if (chillReplies[i].role === 'assistant' && chillReplies[i].content.trim()) return chillReplies[i].content;
    }
    return null;
  }, [chillReplies]);
  // Cards the assistant showed this turn (Show Card): tables, charts, buttons.
  const chillCards = useMemo(() => {
    const out: { key: string; card: NonNullable<ToolUse['card']> }[] = [];
    for (const message of chillReplies) {
      (message.tools ?? []).forEach((tool, j) => {
        if (tool.status === 'done' && tool.card) out.push({ key: `${message.id}:${tool.id || j}`, card: tool.card });
      });
    }
    return out;
  }, [chillReplies]);
  const chillLastFiles = useMemo(() => {
    for (let i = chillReplies.length - 1; i >= 0; i -= 1) {
      const files = chillReplies[i].filesMade;
      if (files?.length) return files;
    }
    return [];
  }, [chillReplies]);
  // Previews under the answer: what this turn shared or published.
  const chillMade = useMemo(() => ({ ...madeInTurn(chillReplies), usedComputer: usedComputer(chillReplies) }), [chillReplies]);

  const switchToFull = useCallback(() => {
    setUiMode('full');
    stopSpeaking();
    navigate(activeSessionId && !incognito ? `/home?chat=${encodeURIComponent(activeSessionId)}` : '/home');
  }, [activeSessionId, incognito, navigate]);
  const switchToChill = useCallback(() => {
    setUiMode('chill');
    navigate(CHILL_PATH);
  }, [navigate]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isModeSwitchKey(event)) return;
      event.preventDefault();
      if (chill) switchToFull();
      else switchToChill();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [chill, switchToFull, switchToChill]);
  useEffect(() => { if (chill) setUiMode('chill'); }, [chill]);

  const chillComposer = (
    <div className="relative">
      {attachments.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5 px-1">
          {attachments.map((a) => (
            <span key={a.id} className="chill-glass inline-flex max-w-full items-center gap-1.5 rounded-full py-1 pl-1 pr-2 text-[12px]">
              {a.kind === 'image' && a.previewUrl
                ? <img src={a.previewUrl} alt="" className="h-6 w-6 rounded-full object-cover" />
                : <span aria-hidden className="grid h-6 w-6 place-items-center rounded-full bg-panel-2 text-fg-dim">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>
                </span>}
              <span className="max-w-[180px] truncate" title={a.name}>{a.name}</span>
              <button type="button" onClick={() => removeAttachment(a.id)} aria-label={tr('ui.home.removeName', { name: a.name })}
                className="grid h-9 w-9 place-items-center rounded-full text-fg-dim hover:text-danger-ink md:h-5 md:w-5">✕</button>
            </span>
          ))}
        </div>
      )}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); void addFiles(e.dataTransfer.files); }}
        className={cn('chill-composer overflow-hidden rounded-[26px] border transition-colors', dragOver ? 'border-accent' : 'focus-within:border-fg-dim')}
      >
        {voice.active && <div className="px-2 pt-1"><VoiceBar voice={voice} /></div>}
        {liveVoice && (
          <div className="border-b border-line px-1">
            <LiveVoicePanel live={live} approvals={approvals} question={question} onExit={() => setLiveVoice(false)} />
          </div>
        )}
        <div className={cn('flex items-end gap-1 p-2', voice.active && '!hidden', empty && 'sm:p-3')}>
          <button type="button" onClick={() => fileInputRef.current?.click()} aria-label={tr('ui.chill.addFiles')} title={tr('ui.chill.addFiles')}
            className="grid h-11 w-11 flex-none place-items-center rounded-full text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg">
            <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
          </button>
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={(e) => {
              const imgs = Array.from(e.clipboardData.files || []).filter((f) => f.type.startsWith('image/'));
              if (imgs.length) { e.preventDefault(); void addFiles(imgs); }
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && busy && !stopping) { e.preventDefault(); void stop(); return; }
              if (isSendKey(e, getSendKey())) { e.preventDefault(); void send(input); }
            }}
            aria-label={tr('ui.chill.messageYourAssistant')}
            placeholder={busy && question === null ? tr('ui.chill.addSomethingWhileItWorks') : isPhone ? tr('ui.chill.askOrJustTalk') : tr('ui.chill.askAnythingOrJustTalk')}
            className={cn('block max-h-48 min-h-11 min-w-0 flex-1 resize-none bg-transparent px-1.5 py-2.5 text-[16px] text-fg outline-none placeholder:text-fg-dim focus-visible:outline-none', empty && 'sm:text-[18px]')}
          />
          {busy && !input.trim() ? (
            <button type="button" onClick={() => void stop()} disabled={stopping}
              aria-label={stopping ? tr('ui.home.stoppingResponse') : tr('ui.home.stopResponse')}
              className="grid h-11 w-11 flex-none place-items-center rounded-full bg-fg text-bg transition disabled:opacity-60">
              <svg aria-hidden width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="3" /></svg>
            </button>
          ) : input.trim() || attachments.length > 0 ? (
            <button type="button" onClick={() => void send(input)} disabled={!!uploadError || !isConnected}
              aria-label={tr('ui.home.send')}
              className="grid h-11 w-11 flex-none place-items-center rounded-full bg-accent text-accent-foreground transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50">
              <svg aria-hidden width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          ) : null}
          {!input.trim() && !liveVoice && (
            <>
              <MicButton voice={voice} className="h-11 w-11 flex-none rounded-full md:h-11 md:w-11" />
              <button type="button" onClick={() => setLiveVoice(true)} disabled={!isConnected}
                aria-label={tr('ui.liveVoice.liveVoiceMode')} title={tr('ui.liveVoice.liveVoiceTalkHandsFree')}
                className={cn('grid flex-none place-items-center rounded-full bg-accent text-accent-foreground transition hover:brightness-105 disabled:opacity-50', empty ? 'h-12 w-12' : 'h-11 w-11', busy && 'hidden')}>
                <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4" /></svg>
              </button>
            </>
          )}
        </div>
        <input ref={fileInputRef} type="file" multiple accept={ACCEPT_ATTR} className="hidden"
          onChange={(e) => { void addFiles(e.target.files || []); e.target.value = ''; }} />
      </div>
      {uploadError && <div role="alert" className="mt-1.5 px-2 text-[12px] text-danger-ink">{uploadError}</div>}
      {stopError && <div role="alert" className="mt-1.5 px-2 text-[12px] text-danger-ink">{stopError}</div>}
      {steerError && <div role="alert" className="mt-1.5 px-2 text-[12px] text-danger-ink">{steerError}</div>}
      <VoiceNotice voice={voice} />
    </div>
  );

  const chillStage = !chill ? null
    : artifactOpen ? <ArtifactViewer pageId={artifactOpen} variant="panel" onClose={closeStage} onGone={closeArtifact} />
      : fileOpen ? <FileViewer fileId={fileOpen} onClose={closeStage} />
        : changesOpen ? (
          <ChangesPanel state={chatChanges} initialSource={changesOpen.source} initialPath={changesOpen.path} onClose={closeStage} onCopyBack={copyBack} />
        ) : appViewOpen ? (
          <AppViewPanel viewId={appViewOpen} onClose={closeStage} onGone={closeAppView} onSendMessage={(text) => { void send(text); }} />
        ) : computerOpen ? (chatPcDevice
          ? <PcScreenPanel device={chatPcDevice} onClose={closeStage} />
          : <ComputerPanel agents={agents.map((a) => ({ id: String(a.id), name: a.name }))} focusAgentId={agentId}
            takeOverRequest={takeOverRequest} onTakeOverStarted={() => setTakeOverRequest(null)} onClose={closeStage} />
        ) : null;

  // Cards that wait on the person, shared by both layouts.
  const errorCard = error && (
            <div role="alert" className="mx-auto my-4 flex w-[calc(100%-3rem)] max-w-[720px] items-center justify-between gap-3 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">
              <span>{tr('ui.home.connectionErrorTheStreamStopped')}</span>
              <button onClick={reconnect} className="min-h-11 flex-none rounded border border-line px-2 py-0.5 transition-colors hover:border-fg-dim hover:text-fg md:min-h-0">
                {tr('ui.home.retry')}
              </button>
            </div>
          );
  const questionCard = question && (
            <QuestionCard
              question={question}
              onAnswer={(answer) => postQuestionAction(question, 'answer', answer)}
              onCancel={() => postQuestionAction(question, 'cancel')}
              onStopTimer={() => postQuestionAction(question, 'stop_timer')}
            />
          );
  const changesCards = activeSessionId && agentId && (
            <ChangesCards
              sessionId={activeSessionId}
              sources={busy ? [] : chatChanges.changes?.sources ?? []}
              working={chatChanges.working}
              onOpen={openChanges}
              onCopyBack={copyBack}
              onUndoAll={(src) => void chatChanges.act({ action: 'reject', source: src.key })}
            />
          );
  const approvalCards = approvals.map((a) => a.access ? (
            <div key={a.request_id} role="alert" className="mx-auto my-4 w-[calc(100%-3rem)] max-w-[720px]">
              <AccessRequestCard
                access={a.access}
                agentName={a.access.agent || currentAgent?.name}
                onAnswer={(grant) => void respondApproval(a.request_id, !!grant, false, false, undefined, false, grant ?? undefined)}
              />
            </div>
          ) : a.takeover ? (
            <div key={a.request_id} role="alert" className="mx-auto my-4 w-[calc(100%-3rem)] max-w-[720px]">
              <PanelCard
                tag={<>{tr('ui.takeover.tag')}</>}
                title={tr('ui.takeover.title', { name: currentAgent?.name || tr('ui.takeover.anAgent') })}
                subtitle={a.takeover.reason || undefined}
                footer={(
                  <>
                    <PanelButton
                      kind="primary"
                      onClick={() => {
                        const desktop = a.takeover?.desktop || 'main';
                        void respondApproval(a.request_id, true);
                        closeArtifact(); closeAppView(); closeChanges(); closeFile();
                        setTakeOverRequest({ desktop, at: Date.now() });
                        setComputerOpen(true);
                      }}
                    >
                      {tr('ui.takeover.takeOver')}
                    </PanelButton>
                    <span className="grow" />
                    <PanelButton onClick={() => respondApproval(a.request_id, false)}>{tr('ui.takeover.skip')}</PanelButton>
                  </>
                )}
              >
                <p className="text-[12.5px] leading-relaxed text-fg-dim">{tr('ui.takeover.hint')}</p>
              </PanelCard>
            </div>
          ) : (
            <div key={a.request_id} role="alert" className="mx-auto my-4 w-[calc(100%-3rem)] max-w-[720px]">
              <PanelCard
                tone="danger"
                tag={<>{tr('ui.home.approval')}</>}
                title={<>{tr('ui.home.run')}{' '}<span className="text-accent-ink">{humanizeToolName(a.tool_name)}</span>?</>}
                subtitle={a.details || undefined}
                fields={[
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
          ));

  if (chill) {
    return (
      <ChillScreen
        empty={empty && !waiting}
        name={user?.name}
        assistantName={currentAgent?.name || tr('ui.chill.assistant')}
        composer={chillComposer}
        stage={chillStage}
        question={chillQuestion}
        answer={chillAnswer}
        streaming={streaming && chillOnLatest}
        thinking={busy && chillOnLatest && question === null ? (planning || chillDoing || (waiting || !chillAnswer ? tr('ui.home.thinking') : null)) : null}
        helper={chillOnLatest ? chillHelper : null}
        listening={liveVoice ? { phase: live.phase, heard: live.heard } : null}
        cards={(errorCard || questionCard || approvals.length > 0) ? <div className="chill-cards">{errorCard}{questionCard}{approvalCards}</div> : null}
        extras={(
          <>
            {chillCards.length > 0 && (
              <div className="mb-4 space-y-4">
                {chillCards.map(({ key, card }) => <InfoCard key={key} card={card} />)}
              </div>
            )}
            {chillMade.usedComputer && agentDesktop && !chatPcDevice && (
              <ComputerPreview desktop={agentDesktop.name} label={currentAgent?.name || tr('ui.chill.assistant')}
                onOpen={showComputer} className="mb-3 max-w-md" />
            )}
            {!(streaming && chillOnLatest) && (chillMade.pages.length > 0 || chillMade.files.length > 0 || chillLastFiles.length > 0) && (
              <div className="space-y-2">
                {chillMade.pages.map((page) => <ChillPageCard key={page.id} {...page} />)}
                <FileCards files={chillMade.files} />
                <FilesShelf files={chillLastFiles.filter((f) => !f.shown)} />
              </div>
            )}
            {changesCards}
          </>
        )}
        rail={chillStripItems}
        railActive={chillActive}
        turns={chillTurns}
        turnIndex={chillTurnIndex}
        onTurn={pickChillTurn}
        replying={busy && !chillOnLatest}
        onRail={openChillItem}
        suggestions={newChatSuggestions()}
        onSuggestion={(text) => { setInput(text); inputRef.current?.focus(); }}
        onNewChat={() => { closeStage(); newConversation(); }}
        chats={convosSorted.filter((c) => !c.archived).slice(0, 30).map((c) => ({ id: c.id, title: c.title }))}
        activeChatId={activeSessionId}
        onOpenChat={(id) => { closeStage(); switchConversation(id); }}
        onFullMode={switchToFull}
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 overflow-hidden bg-bg text-fg">
      {/* Chat column */}
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {/* The theme's whole drawing, faint, behind messages and the message box */}
        <ChatHorizon className={cn((empty || picking) && 'hidden')} />
        {/* Slim header: who the chat is with and its name, the model, reconnect
            while the stream is down, and the chat's side panel. */}
        <header className="app-page-header flex min-h-14 flex-none flex-row flex-nowrap items-center gap-2 border-b border-line py-2 pl-16 pr-3 md:pl-5">
          <div className="flex min-w-0 flex-1 flex-nowrap items-center gap-2.5 overflow-hidden">
            {currentAgent && (
              <span className="flex flex-none items-center gap-2" aria-label={tr('ui.home.chattingWithName', { name: currentAgent.name })}>
                <AgentBadge name={currentAgent.name} avatar={currentAgent.avatar} />
                <span className="hidden text-[14px] font-semibold sm:inline">{currentAgent.name}</span>
              </span>
            )}
            {currentAgent && <span aria-hidden className="hidden flex-none text-fg-dim sm:inline">/</span>}
            <h1 className={cn('min-w-0 truncate text-[14px]', activeSessionId ? 'text-fg-dim max-sm:font-semibold max-sm:text-fg' : 'text-fg-dim')}>
              {activeSessionId ? (activeChat?.title || tr('ui.home.chat')) : tr('ui.home.newChat')}
            </h1>
          </div>
          <div className="flex flex-none items-center gap-1">
            {isConnected ? (
              <span className="sr-only">{tr('ui.home.connectionStatusConnected')}</span>
            ) : (
              <Button
                variant="ghost"
                size="icon"
                aria-label={reconnecting ? tr('ui.home.reconnectingClickToTryNow') : tr('ui.home.reconnect')}
                title={reconnecting ? tr('ui.home.reconnectingClickToTryNow_6f7c') : tr('ui.home.reconnect')}
                onClick={reconnect}
                className="md:h-8 md:w-auto md:px-2"
              >
                <svg aria-hidden="true" className={cn(reconnecting && 'animate-spin')} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 11a8 8 0 1 0 2 5.5" /><path d="M20 5v6h-6" /></svg>
                <span className="sr-only md:not-sr-only">{reconnecting ? tr('ui.home.reconnecting') : tr('ui.home.reconnect_4804')}</span>
              </Button>
            )}
            {/* Chill / Full, the same switch chill mode shows; a single icon on phones. */}
            <ModeSwitch chill={false} onChill={switchToChill} onFull={() => {}} className="chill hidden sm:flex" />
            <button
              type="button"
              onClick={switchToChill}
              aria-label={tr('ui.chill.switchToChillMode')}
              title={tr('ui.chill.chillModeCtrlShift')}
              className="inline-flex h-11 sm:hidden min-w-11 flex-none items-center justify-center rounded-lg px-2.5 text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:h-9 md:min-w-9"
            >
              <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M8.5 14.5c1 1 2.2 1.5 3.5 1.5s2.5-.5 3.5-1.5" /><path d="M9 9.5h.01M15 9.5h.01" /></svg>
            </button>
            {agentId && !picking && (
              <button
                type="button"
                onClick={() => {
                  if (sidebarShown) { closeArtifact(); closeChanges(); closeFile(); setComputerOpen(false); setSidebar(false); }
                  else setSidebar(true);
                }}
                aria-pressed={sidebarShown}
                aria-label={tr('ui.home.chatSidebar')}
                title={tr('ui.home.valueTheChatSidebarCtrl', { value: sidebarShown ? 'Close' : 'Open' })}
                className={cn(
                  'relative inline-flex h-11 min-w-11 flex-none items-center justify-center gap-2 rounded-lg px-2.5 text-[12.5px] text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:h-9 md:min-w-9',
                  sidebarShown && 'bg-panel-2 text-fg',
                )}
              >
                {agentDesktop && <span aria-hidden className="h-[7px] w-[7px] flex-none animate-pulse rounded-full bg-accent" />}
                {sidebarWaiting.length > 0 && <span aria-hidden className="h-[7px] w-[7px] flex-none rounded-full bg-warn" />}
                {sidebarSummary && !sidebarShown && <span className="hidden whitespace-nowrap xl:inline">{sidebarSummary}</span>}
                <svg aria-hidden width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></svg>
              </button>
            )}
          </div>
        </header>

        {/* Stream */}
        <div
          ref={transcriptRef}
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          className="relative min-h-0 flex-1 overscroll-contain overflow-y-auto"
        >
          {incognito && !picking && (
            <div role="note" className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-dashed border-warn/60 bg-warn/10 px-3 py-2 text-[12.5px] leading-relaxed sm:mx-6">
              <svg aria-hidden="true" className="mt-0.5 flex-none text-warn-ink" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><path d="M3 3l18 18" /></svg>
              <span>
                <span className="font-medium">{tr('ui.home.incognito')}</span>{' '}{tr('ui.home.thisChatIsnTSaved')}
              </span>
            </div>
          )}
          {empty ? (
            <NewChatGreeting
              name={user?.name}
              agents={agents}
              teams={teams}
              currentAgentId={agentId}
              recent={recentTargets}
              onPick={startChatWith}
              onSuggestion={(text) => { setInput(text); inputRef.current?.focus(); }}
            >
              {composer}
            </NewChatGreeting>
          ) : (
            <div className="py-4">
              {messages.map((message, index) => (
                <ChatMessageRow
                  key={message.id}
                  message={message}
                  isLast={index === messages.length - 1}
                  streaming={streaming && index === messages.length - 1 && message.role === 'assistant'}
                  onEditSource={selectArtifactEditSource}
                  selectedEditSourceId={editSource?.type === 'artifact' ? editSource.artifact.id : undefined}
                  onEdit={canRework && isUserTurn(message) ? startRework : undefined}
                  onRegenerate={canRework && index === lastReplyIndex && canRegenerate ? regenerate : undefined}
                  onNextStep={index === messages.length - 1 && index === lastReplyIndex && !busy ? acceptNextStep : undefined}
                  supersededCards={supersededCards.get(message.id)}
                />
              ))}

              {(waiting || (quiet && canGoQuiet)) && (
                <div role="status" className="mx-auto w-full max-w-3xl px-4 py-3 sm:px-6">
                  <div className="flex items-center gap-2.5 text-[13px] text-fg-dim">
                    <span className="think-bars"><i /><i /><i /><i /></span>
                    {waiting ? (planning || tr('ui.home.thinking')) : (planning || tr('ui.home.stillWorking'))}
                  </div>
                </div>
              )}

              {chatSwarms.swarms.filter((s, i) => s.status === 'running' || i === 0).reverse().map((swarm) => (
                <div key={swarm.id} className="mx-auto w-full max-w-3xl px-4 py-2 sm:px-6">
                  <SwarmCard
                    swarm={swarm}
                    onStop={chatSwarms.stop}
                    onStopWorker={chatSwarms.stopWorker}
                    onMerge={chatSwarms.merge}
                    onOpenChat={openWorkerChat}
                    onOpenBoard={(id) => navigate(`/board?board=swarm:${encodeURIComponent(id)}`)}
                  />
                </div>
              ))}

              <WaitingMessages
                messages={waitingMsgs}
                onSendNow={() => void stop()}
                sendingNow={stopping}
                onEdit={(m) => void withdrawWaiting(m, true)}
                onRemove={(m) => void withdrawWaiting(m, false)}
              />
            </div>
          )}

          {errorCard}
          {questionCard}
          {changesCards}
          {approvalCards}
        </div>

        {!empty && (
          <div className="relative flex-none px-3 pb-4 pt-2 sm:px-6">
            {composer}
          </div>
        )}
      </div>

      {artifactOpen && (
        <aside className={cn('fixed inset-0 z-40 flex flex-col border-line',
          !artifactExpanded && 'md:static md:z-auto md:w-[min(46vw,720px)] md:flex-none md:border-l')}>
          <ArtifactViewer
            pageId={artifactOpen}
            variant="panel"
            onClose={closeRightSide}
            onBack={sidebarOpen ? closeArtifact : undefined}
            onGone={closeArtifact}
            expanded={artifactExpanded}
            onToggleExpand={() => setArtifactExpanded((v) => !v)}
          />
        </aside>
      )}
      {fileOpen && !artifactOpen && !changesOpen && !appViewOpen && (
        <aside className={cn('fixed inset-0 z-40 flex flex-col border-line',
          !fileExpanded && 'md:static md:z-auto md:w-[min(46vw,720px)] md:flex-none md:border-l')}>
          <FileViewer
            fileId={fileOpen}
            onClose={closeRightSide}
            onBack={sidebarOpen ? closeFile : undefined}
            expanded={fileExpanded}
            onToggleExpand={() => setFileExpanded((v) => !v)}
          />
        </aside>
      )}
      {changesOpen && !artifactOpen && (
        <aside className={cn('fixed inset-0 z-40 flex flex-col border-line',
          !changesExpanded && 'md:static md:z-auto md:w-[min(46vw,720px)] md:flex-none md:border-l')}>
          <ChangesPanel
            state={chatChanges}
            initialSource={changesOpen.source}
            initialPath={changesOpen.path}
            onClose={closeRightSide}
            onBack={sidebarOpen ? closeChanges : undefined}
            expanded={changesExpanded}
            onToggleExpand={() => setChangesExpanded((v) => !v)}
            onCopyBack={copyBack}
          />
        </aside>
      )}
      {appViewOpen && !artifactOpen && !changesOpen && (
        <aside className={cn('fixed inset-0 z-40 flex flex-col border-line',
          !appViewExpanded && 'md:static md:z-auto md:w-[min(46vw,720px)] md:flex-none md:border-l')}>
          <AppViewPanel
            viewId={appViewOpen}
            onClose={closeRightSide}
            onBack={sidebarOpen ? closeAppView : undefined}
            onGone={closeAppView}
            expanded={appViewExpanded}
            onToggleExpand={() => setAppViewExpanded((v) => !v)}
            onSendMessage={(text) => { void send(text); }}
          />
        </aside>
      )}
      {computerOpen && !artifactOpen && !appViewOpen && !changesOpen && !fileOpen && chatPcDevice && (
        <aside className="fixed inset-0 z-40 flex flex-col border-line md:static md:z-auto md:w-[min(46vw,720px)] md:flex-none md:border-l">
          <PcScreenPanel device={chatPcDevice} onClose={closeRightSide} onBack={sidebarOpen ? () => setComputerOpen(false) : undefined} />
        </aside>
      )}
      {computerOpen && !artifactOpen && !appViewOpen && !changesOpen && !fileOpen && !chatPcDevice && (
        <aside className="fixed inset-0 z-40 flex flex-col border-line md:static md:z-auto md:w-[min(46vw,720px)] md:flex-none md:border-l">
          <ComputerPanel
            agents={agents.map((a) => ({ id: String(a.id), name: a.name }))}
            focusAgentId={agentId}
            takeOverRequest={takeOverRequest}
            onTakeOverStarted={() => setTakeOverRequest(null)}
            onClose={closeRightSide}
            onBack={sidebarOpen ? () => setComputerOpen(false) : undefined}
          />
        </aside>
      )}
      {sidebarOpen && !artifactOpen && !appViewOpen && !computerOpen && !changesOpen && !fileOpen && agentId && !picking && (
        <aside className="fixed inset-0 z-40 flex flex-col border-line md:static md:z-auto md:w-[320px] md:flex-none md:border-l">
          <ChatSidebar
            onClose={() => setSidebar(false)}
            desktop={agentDesktop?.name ?? null}
            sandboxOn={sandboxOn}
            desktopHeldBy={agentDesktop?.held_by}
            pc={chatPcDevice}
            onOpenComputer={() => { closeArtifact(); closeAppView(); closeChanges(); closeFile(); setComputerOpen(true); }}
            waiting={sidebarWaiting}
            onApprove={(id) => void respondApproval(id, true)}
            onDeny={(id) => void respondApproval(id, false)}
            onShowWaiting={() => {
              if (!isWideScreen()) setSidebar(false);
              window.setTimeout(() => transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight }), 0);
            }}
            pages={chatItems?.pages ?? []}
            newPageIds={newPageIds}
            onOpenArtifact={showArtifact}
            appViews={chatAppViews}
            onOpenAppView={showAppView}
            images={chatImages}
            files={chatItems?.files ?? []}
            madeFiles={chatFiles}
            activity={chatActivity}
            swarms={chatSwarms.swarms}
            onStopSwarm={chatSwarms.stop}
            onStopWorker={chatSwarms.stopWorker}
            onMergeWorker={chatSwarms.merge}
            onOpenChat={openWorkerChat}
            busy={busy}
            folders={shownFolders}
            onChangeFolders={changeFolders}
            foldersDisabled={busy}
            sessionId={activeSessionId}
            changes={chatChanges.changes?.sources ?? []}
            onOpenChanges={openChanges}
            about={{
              agentId,
              agentName: agentById.get(agentId)?.name || 'Agent',
              avatar: agentById.get(agentId)?.avatar,
              model: agentModel(agentById.get(agentId)),
              reasoning: reasoning || 'agent default',
              messages: messages.filter((m) => (m.role === 'user' || m.role === 'assistant') && m.content.trim()).length,
              started: chatStarted,
              incognito,
            }}
          />
        </aside>
      )}
    </div>
  );
}
