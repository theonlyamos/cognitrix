import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Pin as PinIcon } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { usePolling } from '@/hooks/usePolling';
import { useOptionalNotifications } from '@/context/NotificationsContext';
import { AgentBadge } from '@/components/StartChat';
import { ChatRowMenu } from '@/components/ChatRowMenu';
import { ChatRail, type RailChat, type RailGroup } from '@/components/ChatRail';
import { exportChat } from '@/lib/chatExport';
import { fmtRelative } from '@/lib/transcript';
import {
  ARCHIVED_KEY, GROUP_PREVIEW, groupChats, groupKey, loadClosedSections, saveClosedSections,
} from '@/lib/chatGroups';
import {
  CHATS_STALE, chatHref, chatsEdited, newChatHref, useChatNav,
} from '@/lib/chatNav';
import type { AgentAvatar } from '@/lib/agentAvatar';
import { cn } from '@/lib/utils';

/** One row of the chat list: a chat with an agent or a team. */
export interface SidebarChat {
  id: string;
  title: string;
  kind: 'agent' | 'team';
  agent_id: string | null;
  team_id: string | null;
  updated_at?: string;
  datetime?: string;
  message_count?: number;
  pinned?: boolean;
  archived?: boolean;
  running?: boolean;
  started_by?: { agent_id: string; agent_name: string } | null;
}

interface Named { id: string; name: string; avatar?: AgentAvatar | null }

export type ChatListMode = 'recent' | 'agent';
const MODE_KEY = 'chatList.mode';
/** Chats the Recent list shows before "Show more". */
const RECENT_PREVIEW = 25;
/** How often the list asks whether any chat was added or changed elsewhere. */
const VERSION_POLL_MS = 4_000;

export function loadChatListMode(): ChatListMode {
  try {
    return localStorage.getItem(MODE_KEY) === 'agent' ? 'agent' : 'recent';
  } catch {
    return 'recent';
  }
}

function saveChatListMode(mode: ChatListMode) {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* ignore */
  }
}

interface SidebarChatsProps {
  /** The sidebar is the slim icon rail: show agent thumbnails with flyouts. */
  collapsed: boolean;
  /** Widen the sidebar again (from the rail). */
  onExpand: () => void;
  /** Called after choosing a chat (closes the phone drawer). */
  onNavigate?: () => void;
}

/**
 * Every chat, with any agent or team, in the app sidebar. Recent lists them
 * newest first; By agent puts them under their agent or team. Collapsed, it
 * is a rail of agent thumbnails that open a flyout of that agent's chats.
 */
export default function SidebarChats({ collapsed, onExpand, onNavigate }: SidebarChatsProps) {
  const navigate = useNavigate();
  const notifications = useOptionalNotifications();
  const { activeChatId, runningIds } = useChatNav();
  const { data: chatsData, refetch, setData: setChatsData } = useResource<SidebarChat[]>('/sessions/chats');
  const { data: agentsData, refetch: refetchAgents } = useResource<Named[]>('/agents');
  const { data: teamsData, refetch: refetchTeams } = useResource<Named[]>('/teams');
  const [mode, setMode] = useState<ChatListMode>(loadChatListMode);
  const [closed, setClosed] = useState<Set<string>>(loadClosedSections);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [archivedOpen, setArchivedOpen] = useState(false);

  // Agents and teams too: one made since the sidebar mounted owns new chats.
  const reread = useCallback(() => {
    void refetch({ silent: true });
    void refetchAgents({ silent: true });
    void refetchTeams({ silent: true });
  }, [refetch, refetchAgents, refetchTeams]);
  useEffect(() => {
    window.addEventListener(CHATS_STALE, reread);
    return () => window.removeEventListener(CHATS_STALE, reread);
  }, [reread]);

  const agentById = useMemo(() => new Map((agentsData || []).filter((a) => a.id).map((a) => [a.id, a])), [agentsData]);
  const teamById = useMemo(() => new Map((teamsData || []).filter((t) => t.id).map((t) => [t.id, t])), [teamsData]);
  const running = useMemo(() => new Set(runningIds), [runningIds]);
  const isRunning = useCallback((c: SidebarChat) => !!c.running || running.has(c.id), [running]);

  const chats = useMemo(() => {
    const rows = (chatsData || []).filter((c) =>
      c.kind === 'team' ? !!c.team_id && teamById.has(c.team_id) : !!c.agent_id && agentById.has(c.agent_id),
    );
    return rows.sort((a, b) =>
      Number(!!b.pinned) - Number(!!a.pinned)
      || String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
  }, [chatsData, agentById, teamById]);

  // Keep the list fresh while anything is running somewhere.
  const anyRunning = chats.some(isRunning);
  useEffect(() => {
    if (!anyRunning) return;
    const timer = window.setInterval(reread, 4_000);
    return () => window.clearInterval(timer);
  }, [anyRunning, reread]);

  // Chats made somewhere else (an agent starting a chat with another agent,
  // a chat app, another tab) show up without a reload: a cheap token changes
  // whenever the list does, and only then is the list re-read.
  const versionRef = useRef<string | null>(null);
  const checkVersion = useCallback(async () => {
    if (typeof document !== 'undefined' && document.hidden) return;
    try {
      const { data } = await api.get<{ version?: string }>('/sessions/chats/version');
      const next = data?.version;
      if (!next) return;
      const previous = versionRef.current;
      if (previous === next) return;
      if (previous !== null) {
        // Take the new token only once the list is fresh, so a failed
        // re-read is tried again on the next tick.
        void refetchAgents({ silent: true });
        void refetchTeams({ silent: true });
        const fresh = await api.get<SidebarChat[]>('/sessions/chats');
        setChatsData(fresh.data ?? []);
      }
      versionRef.current = next;
    } catch {
      /* signed out or engine restarting: try again on the next tick */
    }
  }, [refetchAgents, refetchTeams, setChatsData]);
  useEffect(() => { void checkVersion(); }, [checkVersion]);
  usePolling(checkVersion, VERSION_POLL_MS, true);

  const grouped = useMemo(() => groupChats(chats), [chats]);
  const live = useMemo(() => chats.filter((c) => !c.archived), [chats]);

  const nameOf = (c: SidebarChat) =>
    (c.kind === 'team' ? teamById.get(c.team_id || '')?.name : agentById.get(c.agent_id || '')?.name) || '?';
  const avatarOf = (c: SidebarChat) => (c.kind === 'team' ? undefined : agentById.get(c.agent_id || '')?.avatar);

  const open = (c: SidebarChat) => {
    onNavigate?.();
    navigate(chatHref(c));
  };
  const startWith = (target: { kind: 'agent' | 'team'; id: string }) => {
    onNavigate?.();
    navigate(newChatHref(target));
  };

  const pickMode = (next: ChatListMode) => {
    setMode(next);
    saveChatListMode(next);
  };
  const toggleSection = (key: string) => {
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      saveClosedSections(next);
      return next;
    });
  };

  // ── Row actions ──
  const update = async (c: SidebarChat, changes: { title?: string; pinned?: boolean; archived?: boolean }) => {
    try {
      await api.patch(
        c.kind === 'team'
          ? `/teams/${encodeURIComponent(c.team_id || '')}/chats/${encodeURIComponent(c.id)}`
          : `/sessions/${encodeURIComponent(c.id)}`,
        changes,
      );
    } catch {
      // The list is re-read below and shows what was actually saved.
    }
    reread();
    chatsEdited();
  };
  const saveRename = () => {
    const target = renaming && chats.find((c) => c.id === renaming.id);
    const title = renaming?.title.trim();
    setRenaming(null);
    if (target && title && title !== target.title) void update(target, { title });
  };
  const remove = async (c: SidebarChat) => {
    if (isRunning(c) || !confirm('Delete this conversation?')) return;
    try {
      await api.delete(c.kind === 'team' ? `/teams/${c.team_id}/chats/${c.id}` : `/sessions/${c.id}`);
      reread();
      chatsEdited({ deletedId: c.id });
    } catch {
      // Nothing changed; the list stays as it is.
    }
  };
  // Ask the chat's agent to write up how it did the job as a reusable skill.
  // The draft waits in the Inbox; nothing changes until it's approved.
  const saveAsSkill = async (c: SidebarChat) => {
    notifications?.push({ event: 'agent_alert', title: 'Writing a skill from this chat…', body: c.title });
    try {
      await api.post('/skill-drafts', { session_id: c.id });
      void notifications?.refreshInbox();
    } catch (err) {
      notifications?.push({ event: 'agent_alert', title: "Couldn't write a skill from this chat", body: errorMessage(err, 'Try again in a moment.') });
    }
  };

  // ── Collapsed: the agent rail (wide screens only; the phone drawer keeps the list) ──
  let rail: JSX.Element | null = null;
  if (collapsed) {
    const railChat = (c: SidebarChat): RailChat => ({
      id: c.id,
      title: c.title,
      when: fmtRelative(c.updated_at),
      running: isRunning(c),
      selected: c.kind !== 'team' && c.id === activeChatId,
    });
    const activeChat = chats.find((c) => c.id === activeChatId);
    const groups: RailGroup[] = grouped.groups.map((g) => {
      const named = g.kind === 'team' ? teamById.get(g.id) : agentById.get(g.id);
      return {
        key: g.key,
        kind: g.kind,
        id: g.id,
        name: named?.name || '?',
        avatar: g.kind === 'team' ? undefined : named?.avatar,
        chats: g.chats.map(railChat),
        current: !!activeChat && groupKey(activeChat) === g.key,
      };
    });
    rail = (
      <div className="hidden min-h-0 flex-1 md:flex">
        <ChatRail
          bare
          groups={groups}
          pinned={grouped.pinned.map(railChat)}
          archivedCount={grouped.archived.length}
          onExpand={(key) => {
            if (key === ARCHIVED_KEY) setArchivedOpen(true);
            else if (key) pickMode('agent');
            onExpand();
          }}
          onNew={() => navigate(newChatHref())}
          onNewWith={(g) => startWith({ kind: g.kind, id: g.id })}
          onOpenChat={(id) => {
            const chat = chats.find((c) => c.id === id);
            if (chat) open(chat);
          }}
        />
      </div>
    );
  }

  // ── Full list ──
  const row = (c: SidebarChat, opts: { badge: boolean; indent?: boolean }) => {
    const who = nameOf(c);
    const selected = c.kind !== 'team' && c.id === activeChatId;
    const busy = isRunning(c);
    if (renaming?.id === c.id) {
      return (
        <li key={c.id}>
          <form className={cn('flex items-center py-0.5', opts.indent ? 'pl-8' : 'pl-1')} onSubmit={(e) => { e.preventDefault(); saveRename(); }}>
            <input
              autoFocus
              value={renaming.title}
              maxLength={120}
              aria-label="Chat name"
              onChange={(e) => setRenaming({ id: c.id, title: e.target.value })}
              onBlur={saveRename}
              onKeyDown={(e) => { if (e.key === 'Escape') setRenaming(null); }}
              className="min-h-11 w-full min-w-0 rounded-lg border border-accent bg-bg px-2 text-[13px] text-fg focus:outline-none md:min-h-8"
            />
          </form>
        </li>
      );
    }
    const actions = [
      { label: 'Rename', disabled: busy, onSelect: () => setRenaming({ id: c.id, title: c.title }) },
      { label: c.pinned ? 'Unpin' : 'Pin to top', disabled: busy, onSelect: () => void update(c, { pinned: !c.pinned }) },
      { label: c.archived ? 'Move back to chats' : 'Archive', disabled: busy, onSelect: () => void update(c, { archived: !c.archived }) },
      ...(c.kind === 'team' ? [] : [
        { label: 'Export as Markdown', onSelect: () => void exportChat(c.id, c.title, who).catch(() => {}) },
        { label: 'Save as skill', disabled: busy, onSelect: () => void saveAsSkill(c) },
      ]),
      { label: 'Delete', danger: true, disabled: busy, onSelect: () => void remove(c) },
    ];
    return (
      <li key={c.id} className="group relative" data-chat-row={c.id}>
        <button
          type="button"
          onClick={() => open(c)}
          aria-current={selected ? 'page' : undefined}
          title={c.started_by ? `${c.title} · started by ${c.started_by.agent_name || 'an agent'}` : c.title}
          className={cn(
            'flex min-h-11 w-full items-center gap-2.5 rounded-lg pr-9 text-left text-[13.5px] transition-colors md:min-h-[34px]',
            opts.indent ? 'pl-9' : 'pl-2.5',
            selected ? 'bg-panel-2 font-medium text-fg' : 'text-fg/90 hover:bg-panel-2',
          )}
        >
          {opts.badge && <AgentBadge name={who} team={c.kind === 'team'} avatar={avatarOf(c)} size="sm" />}
          {opts.badge && <span className="sr-only">{who}: </span>}
          <span className="min-w-0 flex-1 truncate">{c.title}</span>
          {c.pinned && <PinIcon aria-label="Pinned" className="h-3 w-3 flex-none text-fg-dim" strokeWidth={2} />}
          {busy && <span aria-label="working" title="Working" data-testid={`running-${c.id}`} className="h-1.5 w-1.5 flex-none animate-pulse rounded-full bg-accent" />}
        </button>
        <div className={cn('absolute inset-y-0 right-0 flex items-center transition-opacity', selected ? 'opacity-100' : 'md:opacity-0 md:focus-within:opacity-100 md:group-hover:opacity-100')}>
          <ChatRowMenu compact label={`More actions for ${c.title}`} actions={actions} />
        </div>
      </li>
    );
  };

  const shownRecent = showAll ? live : live.slice(0, RECENT_PREVIEW);

  return (
    <>
    {rail}
    <div className={cn('flex min-h-0 flex-1 flex-col', collapsed && 'md:hidden')}>
      <div className="flex flex-none items-center justify-between gap-2 px-2.5 pb-1.5 pt-4">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-dim">Chats</span>
        <div role="group" aria-label="List chats by" className="flex rounded-lg bg-panel-2 p-0.5 text-[12px]">
          {(['recent', 'agent'] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => pickMode(m)}
              className={cn('min-h-8 rounded-md px-2.5 transition-colors md:min-h-6', mode === m ? 'bg-panel font-semibold text-fg shadow-sm' : 'text-fg-dim hover:text-fg')}
            >
              {m === 'recent' ? 'Recent' : 'By agent'}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-0.5 pb-2 [scrollbar-width:thin]">
        {chats.length === 0 ? (
          <p className="px-2.5 py-2 text-[12.5px] text-fg-dim">{chatsData ? 'No chats yet.' : 'Loading…'}</p>
        ) : mode === 'recent' ? (
          <ul aria-label="Recent chats" className="flex flex-col gap-px">
            {shownRecent.map((c) => row(c, { badge: true }))}
            {live.length > shownRecent.length && (
              <li>
                <button type="button" onClick={() => setShowAll(true)} className="min-h-9 w-full rounded-lg px-2.5 text-left text-[12.5px] text-fg-dim hover:bg-panel-2 hover:text-fg">
                  Show {live.length - shownRecent.length} more
                </button>
              </li>
            )}
          </ul>
        ) : (
          <div className="flex flex-col gap-1">
            {grouped.pinned.length > 0 && (
              <ul aria-label="Pinned chats" className="flex flex-col gap-px">
                {grouped.pinned.map((c) => row(c, { badge: true }))}
              </ul>
            )}
            {grouped.groups.map((g) => {
              const named = g.kind === 'team' ? teamById.get(g.id) : agentById.get(g.id);
              const name = named?.name || '?';
              const isOpen = !closed.has(g.key);
              const limit = expanded.has(g.key) ? Infinity : GROUP_PREVIEW;
              const shown = g.chats.slice(0, limit);
              const busyHere = g.chats.some(isRunning);
              const sectionId = `sidebar-section-${g.key.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
              return (
                <section key={g.key} aria-label={`${name} chats`} data-chat-section={g.key}>
                  <div className="group/sec flex items-center rounded-lg hover:bg-panel-2">
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      aria-controls={sectionId}
                      onClick={() => toggleSection(g.key)}
                      className="flex min-h-11 min-w-0 flex-1 items-center gap-2 pl-1.5 text-left md:min-h-[34px]"
                    >
                      <svg aria-hidden width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" className={cn('flex-none text-fg-dim transition-transform', !isOpen && '-rotate-90')}><path d="m6 9 6 6 6-6" /></svg>
                      <AgentBadge name={name} team={g.kind === 'team'} avatar={g.kind === 'team' ? undefined : named?.avatar} size="sm" />
                      <span className="min-w-0 flex-1 truncate text-[13.5px] font-semibold">{name}</span>
                      {busyHere && <span aria-label="working" className="h-1.5 w-1.5 flex-none animate-pulse rounded-full bg-accent" />}
                      <span className="flex-none text-[11.5px] text-fg-dim">{g.chats.length}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => startWith({ kind: g.kind, id: g.id })}
                      aria-label={`New chat with ${name}`}
                      title={`New chat with ${name}`}
                      className="mx-1 grid h-11 w-11 flex-none place-items-center rounded-md text-fg-dim hover:text-fg md:h-6 md:w-6 md:opacity-0 md:focus:opacity-100 md:group-hover/sec:opacity-100"
                    >
                      <svg aria-hidden width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
                    </button>
                  </div>
                  {isOpen && (
                    <ul id={sectionId} className="flex flex-col gap-px">
                      {shown.map((c) => row(c, { badge: false, indent: true }))}
                      {g.chats.length > shown.length && (
                        <li>
                          <button
                            type="button"
                            onClick={() => setExpanded((prev) => new Set(prev).add(g.key))}
                            className="min-h-9 w-full rounded-lg pl-9 text-left text-[12.5px] text-fg-dim hover:bg-panel-2 hover:text-fg"
                          >
                            Show {g.chats.length - shown.length} more
                          </button>
                        </li>
                      )}
                    </ul>
                  )}
                </section>
              );
            })}
          </div>
        )}
        {grouped.archived.length > 0 && (
          <div className="mt-2">
            <button
              type="button"
              aria-expanded={archivedOpen}
              onClick={() => setArchivedOpen((v) => !v)}
              className="flex min-h-9 w-full items-center gap-2 rounded-lg px-2.5 text-left text-[12.5px] text-fg-dim hover:bg-panel-2 hover:text-fg"
            >
              <svg aria-hidden width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" className={cn('flex-none transition-transform', !archivedOpen && '-rotate-90')}><path d="m6 9 6 6 6-6" /></svg>
              Archived ({grouped.archived.length})
            </button>
            {archivedOpen && (
              <ul aria-label="Archived chats" className="flex flex-col gap-px">
                {grouped.archived.map((c) => row(c, { badge: true }))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
    </>
  );
}
