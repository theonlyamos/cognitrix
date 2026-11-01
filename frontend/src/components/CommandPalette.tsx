import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

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

interface Item {
  id: string;
  group: 'Actions' | 'Agents' | 'Tasks' | 'Teams';
  label: string;
  hint?: string;
  shortcut?: string;
  keywords?: string;
  run: () => void;
}

/** Opens a fresh chat with an agent (Home reads its agent from localStorage). */
export function startChatWith(agentId: string, navigate: (to: string) => void, onHome: boolean) {
  try {
    localStorage.setItem('selectedAgentId', agentId);
    localStorage.removeItem(`chatSession:${agentId}`);
  } catch {
    /* ignore */
  }
  if (onHome) window.location.assign('/home');
  else navigate('/home');
}

/** Ctrl K: search actions, agents, tasks and teams in one list. */
export default function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [tasks, setTasks] = useState<TaskLite[]>([]);
  const [teams, setTeams] = useState<TeamLite[]>([]);
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
  }, [open]);

  const items = useMemo<Item[]>(() => {
    const go = (to: string) => () => navigate(to);
    const actions: Item[] = [
      { id: 'new-chat', group: 'Actions', label: 'New chat', hint: 'Talk to your selected agent', shortcut: 'Ctrl N', run: go('/home') },
      { id: 'run-task', group: 'Actions', label: 'Run task…', hint: 'Plan and execute with an agent or team in the background', run: go('/tasks/new') },
      { id: 'inbox', group: 'Actions', label: 'Open inbox', hint: 'Approvals, questions and agent alerts waiting on you', run: go('/inbox') },
      { id: 'automations', group: 'Actions', label: 'Open automations', hint: 'Scheduled, self-checking and webhook tasks', run: go('/automations') },
      { id: 'new-agent', group: 'Actions', label: 'New agent', run: go('/agents/new') },
      { id: 'new-team', group: 'Actions', label: 'New team', run: go('/teams/new') },
      { id: 'notifications', group: 'Actions', label: 'Notifications', keywords: 'alerts bell', run: go('/notifications') },
      { id: 'settings', group: 'Actions', label: 'Settings', keywords: 'engine providers keys sign out preferences', run: go('/settings') },
      { id: 'api-keys', group: 'Actions', label: 'API keys', keywords: 'tokens access', run: go('/api-keys') },
    ];
    const agentItems: Item[] = agents.map((a) => ({
      id: `agent-${a.id}`,
      group: 'Agents',
      label: `New chat with ${a.name}`,
      hint: [a.llm?.provider, a.llm?.model].filter(Boolean).join(' · '),
      run: () => startChatWith(a.id, navigate, pathname === '/home'),
    }));
    const taskItems: Item[] = tasks.map((t) => ({
      id: `task-${t.id}`,
      group: 'Tasks',
      label: t.title,
      hint: t.status?.replace('_', ' '),
      run: go(`/tasks/${t.id}`),
    }));
    const teamItems: Item[] = teams.flatMap((t) => [
      { id: `team-${t.id}`, group: 'Teams' as const, label: t.name, run: go(`/teams/${t.id}`) },
      { id: `team-chat-${t.id}`, group: 'Teams' as const, label: `Group chat: ${t.name}`, run: go(`/teams/${t.id}/chat`) },
    ]);
    const all = [...actions, ...agentItems, ...taskItems, ...teamItems];
    const q = query.trim().toLowerCase();
    if (!q) return [...actions, ...agentItems.slice(0, 4), ...taskItems.slice(0, 5)];
    return all.filter((item) => `${item.label} ${item.hint ?? ''} ${item.keywords ?? ''}`.toLowerCase().includes(q)).slice(0, 30);
  }, [agents, tasks, teams, query, navigate, pathname]);

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
        aria-label="Command palette"
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
            placeholder="Type a command, agent or task"
            aria-label="Search commands, agents and tasks"
            aria-controls="palette-results"
            aria-activedescendant={items[active] ? `palette-${items[active].id}` : undefined}
            className="flex-1 border-0 bg-transparent text-[17px] text-fg outline-none placeholder:text-fg-dim"
          />
          <kbd className="rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim">Esc</kbd>
        </div>
        <div ref={listRef} id="palette-results" role="listbox" aria-label="Results" className="max-h-[52vh] overflow-y-auto p-2">
          {items.length === 0 && <p className="px-3 py-8 text-center text-[13px] text-fg-dim">No matches.</p>}
          {items.map((item, index) => {
            const header = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return (
              <div key={item.id}>
                {header && (
                  <div className="px-2.5 pb-1 pt-3 font-mono text-[10px] tracking-[0.14em] text-fg-dim first:pt-1.5">{header.toUpperCase()}</div>
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
          <span>↑↓ navigate</span>
          <span>Enter select</span>
          <span className="flex-1" />
          <span>Ctrl K to toggle</span>
        </div>
      </div>
    </div>
  );
}
