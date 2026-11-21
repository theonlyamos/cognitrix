import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { chatHref, newChatHref } from '@/lib/chatNav';
import { modelName, statusLabel } from '@/lib/plain';
import { tr } from '@/lib/i18n';

interface AgentLite {
  id: string;
  name: string;
  llm?: { provider?: string; model?: string };
}

interface TaskLite {
  id: string;
  title: string;
  status?: string;
}

interface TeamLite {
  id: string;
  name: string;
}

interface ChatLite {
  id: string;
  title: string;
  kind?: 'agent' | 'team';
  team_id?: string | null;
  archived?: boolean;
}

/** A chat found by words in its messages. */
interface ChatHit {
  id: string;
  title: string;
  snippet?: string;
}

interface Item {
  id: string;
  group: 'Actions' | 'Chats' | 'Agents' | 'Tasks' | 'Teams' | 'Projects' | 'Library' | 'Memory' | 'Settings';
  label: string;
  hint?: string;
  shortcut?: string;
  keywords?: string;
  run: () => void;
}

const GROUP_WORDS: Record<Item['group'], { label: string }> = {
  Actions: { get label() { return tr('ui.commandPalette.actions'); } }, Chats: { get label() { return tr('ui.commandPalette.chats'); } }, Agents: { get label() { return tr('ui.commandPalette.agents'); } }, Tasks: { get label() { return tr('ui.commandPalette.tasks'); } }, Teams: { get label() { return tr('ui.commandPalette.teams'); } },
  Projects: { get label() { return tr('ui.commandPalette.projects'); } }, Library: { get label() { return tr('ui.commandPalette.library'); } }, Memory: { get label() { return tr('ui.commandPalette.memory'); } }, Settings: { get label() { return tr('ui.commandPalette.settings'); } },
};

interface Named { id: string; name: string; description?: string }
interface PageLite { id: string; title: string }
interface MemoryLite { id: string; content: string }

/** Ctrl K: one search over everything: chats, agents, tasks, teams, projects, the library, memories and settings. */
export default function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [tasks, setTasks] = useState<TaskLite[]>([]);
  const [teams, setTeams] = useState<TeamLite[]>([]);
  const [chats, setChats] = useState<ChatLite[]>([]);
  const [projects, setProjects] = useState<Named[]>([]);
  const [pages, setPages] = useState<PageLite[]>([]);
  const [apps, setApps] = useState<PageLite[]>([]);
  const [collections, setCollections] = useState<Named[]>([]);
  const [memories, setMemories] = useState<MemoryLite[]>([]);
  const [hits, setHits] = useState<ChatHit[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setActive(0);
    requestAnimationFrame(() => inputRef.current?.focus());
    // Fresh lists each time it opens; failures just leave a group empty.
    api.get<AgentLite[]>('/agents').then((r) => setAgents(r.data ?? [])).catch(() => {});
    api.get<TaskLite[]>('/tasks').then((r) => setTasks(r.data ?? [])).catch(() => {});
    api.get<TeamLite[]>('/teams').then((r) => setTeams(r.data ?? [])).catch(() => {});
    api.get<ChatLite[]>('/sessions/chats').then((r) => setChats(r.data ?? [])).catch(() => {});
    api.get<{ projects: Named[] }>('/projects').then((r) => setProjects(r.data?.projects ?? [])).catch(() => {});
    api.get<{ pages: PageLite[] }>('/artifact-pages').then((r) => setPages(r.data?.pages ?? [])).catch(() => {});
    api.get<{ apps: PageLite[] }>('/hosted-apps').then((r) => setApps(r.data?.apps ?? [])).catch(() => {});
    api.get<Named[] | { collections: Named[] }>('/knowledge')
      .then((r) => setCollections(Array.isArray(r.data) ? r.data : r.data?.collections ?? []))
      .catch(() => {});
    api.get<{ memories: MemoryLite[] }>('/memories').then((r) => setMemories(r.data?.memories ?? [])).catch(() => {});
  }, [open]);

  // Words in a chat's messages, not just its title (three letters or more).
  useEffect(() => {
    const words = query.trim();
    if (!open || words.length < 3) {
      setHits([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      api.get<ChatHit[]>('/sessions/search', { params: { q: words } })
        .then((r) => { if (!cancelled) setHits(r.data ?? []); })
        .catch(() => { if (!cancelled) setHits([]); });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, query]);

  const items = useMemo<Item[]>(() => {
    const go = (to: string) => () => navigate(to);
    const actions: Item[] = [
      { id: 'new-chat', group: 'Actions', label: tr('ui.commandPalette.newChat'), hint: tr('ui.commandPalette.talkToYourSelectedAgent'), shortcut: 'Ctrl N', run: go(newChatHref()) },
      { id: 'run-task', group: 'Actions', label: tr('ui.commandPalette.runTask'), hint: tr('ui.commandPalette.planAndExecuteWithAn'), run: go('/tasks/new') },
      { id: 'inbox', group: 'Actions', label: tr('ui.commandPalette.openInbox'), hint: tr('ui.commandPalette.approvalsQuestionsAndAgentAlerts'), run: go('/inbox') },
      { id: 'scheduled', group: 'Actions', label: tr('ui.commandPalette.scheduledTasks'), hint: tr('ui.commandPalette.tasksThatRunOnTheir'), keywords: 'automations schedule recurring webhooks', run: go('/tasks?show=scheduled') },
      { id: 'new-agent', group: 'Actions', label: tr('ui.commandPalette.newAgent'), run: go('/agents/new') },
      { id: 'new-team', group: 'Actions', label: tr('ui.commandPalette.newTeam'), run: go('/teams/new') },
      { id: 'library', group: 'Actions', label: tr('ui.commandPalette.openTheLibrary'), hint: tr('ui.commandPalette.appsPagesImagesDocumentsAnd'), keywords: 'artifacts apps knowledge', run: go('/apps') },
    ];
    const settings: Item[] = [
      { id: 'settings', group: 'Settings', label: tr('ui.commandPalette.settings'), keywords: 'preferences', run: go('/settings') },
      { id: 'settings-account', group: 'Settings', label: tr('ui.commandPalette.accountAndDevices'), keywords: 'password sign out export profile signed in sessions', run: go('/settings/account') },
      { id: 'settings-appearance', group: 'Settings', label: tr('ui.commandPalette.appearance'), keywords: 'theme dark light language', run: go('/settings/appearance') },
      { id: 'settings-notifications', group: 'Settings', label: tr('ui.commandPalette.notifications'), keywords: 'alerts bell push', run: go('/settings/notifications') },
      { id: 'settings-models', group: 'Settings', label: tr('ui.commandPalette.modelsAndKeys'), keywords: 'provider api key openai anthropic gemini groq chatgpt', run: go('/settings/models') },
      { id: 'settings-usage', group: 'Settings', label: tr('ui.commandPalette.usageAndLimits'), keywords: 'cost spend tokens money helpers swarm', run: go('/settings/usage') },
      { id: 'settings-assistant', group: 'Settings', label: tr('ui.commandPalette.assistant'), keywords: 'check in morning brief quiet hours proactive', run: go('/settings/assistant') },
      { id: 'settings-memory', group: 'Settings', label: tr('ui.commandPalette.memoryAndSkills'), keywords: 'remember forget tidy instructions', run: go('/settings/memory') },
      { id: 'settings-voice', group: 'Settings', label: tr('ui.commandPalette.voice'), keywords: 'microphone dictation spoken replies', run: go('/settings/voice') },
      { id: 'settings-connections', group: 'Settings', label: tr('ui.commandPalette.connections'), keywords: 'connected apps github gmail google mcp passwords telegram slack email', run: go('/settings/apps') },
      { id: 'settings-computers', group: 'Settings', label: tr('ui.commandPalette.computers'), keywords: 'pc pair sandbox working folder', run: go('/settings/computers') },
      { id: 'api-keys', group: 'Settings', label: tr('ui.commandPalette.developerKeys'), keywords: 'api tokens access scripts', run: go('/api-keys') },
    ];
    const agentItems: Item[] = agents.map((a) => ({
      id: `agent-${a.id}`,
      group: 'Agents',
      label: tr('ui.commandPalette.newChatWithName', { name: a.name }),
      hint: modelName(a.llm?.model),
      run: go(newChatHref({ kind: 'agent', id: a.id })),
    }));
    const taskItems: Item[] = tasks.map((t) => ({
      id: `task-${t.id}`,
      group: 'Tasks',
      label: t.title,
      hint: statusLabel(t.status),
      run: go(`/tasks/${t.id}`),
    }));
    const teamItems: Item[] = teams.flatMap((t) => [
      { id: `team-${t.id}`, group: 'Teams' as const, label: t.name, run: go(`/teams/${t.id}`) },
      { id: `team-chat-${t.id}`, group: 'Teams' as const, label: tr('ui.commandPalette.groupChatName', { name: t.name }), run: go(`/teams/${t.id}/chat`) },
    ]);
    const projectItems: Item[] = projects.map((p) => ({ id: `project-${p.id}`, group: 'Projects', label: p.name, hint: p.description, run: go(`/projects/${p.id}`) }));
    const libraryItems: Item[] = [
      ...apps.map((a) => ({ id: `app-${a.id}`, group: 'Library' as const, label: a.title, hint: tr('ui.commandPalette.app'), run: go(`/apps/${a.id}`) })),
      ...pages.map((a) => ({ id: `page-${a.id}`, group: 'Library' as const, label: a.title, hint: tr('ui.commandPalette.page'), run: go(`/artifacts/${a.id}`) })),
      ...collections.map((c) => ({ id: `knowledge-${c.id}`, group: 'Library' as const, label: c.name, hint: tr('ui.commandPalette.knowledge'), run: go('/apps?tab=knowledge') })),
    ];
    const memoryItems: Item[] = memories.map((m) => ({ id: `memory-${m.id}`, group: 'Memory', label: m.content, run: go('/settings/memory') }));
    const byId = new Map(chats.map((c) => [c.id, c]));
    const chatItems: Item[] = chats.filter((c) => !c.archived).map((c) => ({
      id: `chat-${c.id}`,
      group: 'Chats',
      label: c.title,
      run: go(chatHref(c)),
    }));
    const q = query.trim().toLowerCase();
    if (!q) return [...actions, ...chatItems.slice(0, 5), ...agentItems.slice(0, 4), ...taskItems.slice(0, 5)];
    const matches = (item: Item) => `${item.label} ${item.hint ?? ''} ${item.keywords ?? ''}`.toLowerCase().includes(q);
    const titled = chats.filter((c) => c.title.toLowerCase().includes(q)).map((c) => ({
      id: `chat-${c.id}`, group: 'Chats' as const, label: c.title, run: go(chatHref(c)),
    }));
    const found = hits.filter((h) => !titled.some((t) => t.id === `chat-${h.id}`)).map((h) => ({
      id: `chat-${h.id}`, group: 'Chats' as const, label: h.title, hint: h.snippet, run: go(chatHref(byId.get(h.id) || h)),
    }));
    return [
      ...actions.filter(matches),
      ...[...titled, ...found].slice(0, 8),
      ...[...agentItems, ...taskItems, ...teamItems, ...projectItems, ...libraryItems].filter(matches),
      ...memoryItems.filter(matches).slice(0, 4),
      ...settings.filter(matches),
    ].slice(0, 40);
  }, [agents, tasks, teams, chats, hits, projects, pages, apps, collections, memories, query, navigate]);

  useEffect(() => setActive(0), [query]);

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [active]);

  if (!open) return null;

  const choose = (item: Item | undefined) => {
    if (!item) return;
    onClose();
    item.run();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => Math.min(items.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      choose(items[active]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  };

  let lastGroup = '';

  return (
    <div className="fixed inset-0 z-[70] bg-[rgba(5,6,7,.55)]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={tr('ui.commandPalette.commandPalette')}
        onMouseDown={(e) => e.stopPropagation()}
        className="mx-auto mt-[12vh] w-[660px] max-w-[calc(100vw-24px)] overflow-hidden rounded-2xl border border-line bg-panel shadow-panel"
      >
        <div className="flex items-center gap-3 border-b border-line px-4 py-3.5">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-fg-dim" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={tr('ui.commandPalette.searchChatsAgentsTasksOr')}
            aria-label={tr('ui.commandPalette.searchChatsCommandsAgentsAnd')}
            aria-controls="palette-results"
            aria-activedescendant={items[active] ? `palette-${items[active].id}` : undefined}
            className="flex-1 border-0 bg-transparent text-[17px] text-fg outline-none placeholder:text-fg-dim"
          />
          <kbd className="rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim">Esc</kbd>
        </div>
        <div ref={listRef} id="palette-results" role="listbox" aria-label={tr('ui.commandPalette.results')} className="max-h-[52vh] overflow-y-auto p-2">
          {items.length === 0 && <p className="px-3 py-8 text-center text-[13px] text-fg-dim">{tr('ui.commandPalette.noMatches')}</p>}
          {items.map((item, index) => {
            const header = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return (
              <div key={item.id}>
                {header && (
                  <div className="px-2.5 pb-1 pt-3 text-[12px] font-medium text-fg-dim first:pt-1.5">{GROUP_WORDS[header].label}</div>
                )}
                <div
                  id={`palette-${item.id}`}
                  role="option"
                  aria-selected={index === active}
                  data-index={index}
                  onMouseMove={() => setActive(index)}
                  onClick={() => choose(item)}
                  className={cn(
                    'flex cursor-pointer items-center gap-3 rounded-[10px] px-2.5 py-2',
                    index === active && 'bg-accent/10',
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14px] font-medium">{item.label}</span>
                    {item.hint && <span className="block truncate text-[12px] text-fg-dim">{item.hint}</span>}
                  </span>
                  {item.shortcut && <span className="font-mono text-[10.5px] text-fg-dim">{item.shortcut}</span>}
                </div>
              </div>
            );
          })}
        </div>
        <div className="flex gap-4 border-t border-line bg-panel-2/60 px-4 py-2.5 font-mono text-[11px] text-fg-dim">
          <span>{tr('ui.commandPalette.navigate')}</span>
          <span>{tr('ui.commandPalette.enterSelect')}</span>
          <span className="flex-1" />
          <span>{tr('ui.commandPalette.ctrlKToToggle')}</span>
        </div>
      </div>
    </div>
  );
}
