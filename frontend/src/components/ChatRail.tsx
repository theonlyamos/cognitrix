import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import type { AgentAvatar } from '@/lib/agentAvatar';
import { AgentBadge } from '@/components/StartChat';

export interface RailChat {
  id: string;
  title: string;
  /** "2h ago", or what the row shows instead of a time. */
  when: string;
  running?: boolean;
  selected?: boolean;
}

export interface RailGroup {
  key: string;
  kind: 'agent' | 'team';
  id: string;
  name: string;
  avatar?: AgentAvatar | null;
  chats: RailChat[];
  /** The chat on screen is with this agent. */
  current?: boolean;
}

interface ChatRailProps {
  groups: RailGroup[];
  pinned: RailChat[];
  archivedCount: number;
  /** Back to the full list; with a key, with that section open and in view. */
  onExpand: (sectionKey?: string) => void;
  onNew: () => void;
  onNewWith: (group: RailGroup) => void;
  onOpenChat: (chatId: string) => void;
}

const FLYOUT_WIDTH = 300;
const PINNED = '__pinned';

/** The chat list, minimized: one thumbnail per agent and team. A thumbnail
 *  opens a flyout with that agent's chats, so the list never has to widen. */
export function ChatRail({ groups, pinned, archivedCount, onExpand, onNew, onNewWith, onOpenChat }: ChatRailProps) {
  const [open, setOpen] = useState<{ key: string; top: number; left: number } | null>(null);
  const railRef = useRef<HTMLElement>(null);
  const flyoutRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef(0);
  const openTimer = useRef(0);

  const clearTimers = () => {
    window.clearTimeout(closeTimer.current);
    window.clearTimeout(openTimer.current);
  };
  useEffect(() => clearTimers, []);

  const show = useCallback((key: string, anchor: HTMLElement) => {
    const rect = anchor.getBoundingClientRect();
    setOpen({ key, top: rect.top, left: rect.right + 8 });
  }, []);
  const close = useCallback(() => {
    clearTimers();
    setOpen(null);
  }, []);

  // Hover opens after a beat and closes a beat after the pointer leaves both
  // the thumbnail and the flyout; a click or the keyboard opens it at once.
  const hoverIn = (key: string, anchor: HTMLElement) => {
    clearTimers();
    openTimer.current = window.setTimeout(() => show(key, anchor), open ? 60 : 220);
  };
  const hoverOut = () => {
    clearTimers();
    closeTimer.current = window.setTimeout(() => setOpen(null), 260);
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!flyoutRef.current?.contains(target) && !railRef.current?.contains(target)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      const key = open.key;
      close();
      railRef.current?.querySelector<HTMLElement>(`[data-rail-key="${CSS.escape(key)}"]`)?.focus();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', close);
    };
  }, [open, close]);

  // Keep the flyout on screen when its thumbnail is near the bottom.
  const [shift, setShift] = useState(0);
  useEffect(() => {
    if (!open || !flyoutRef.current) {
      setShift(0);
      return;
    }
    const overflow = open.top + flyoutRef.current.offsetHeight + 12 - window.innerHeight;
    setShift(overflow > 0 ? Math.min(overflow, open.top - 8) : 0);
  }, [open]);

  const tile = (key: string, label: string, content: ReactNode, opts: { current?: boolean; running?: boolean } = {}) => (
    <button
      key={key}
      type="button"
      data-rail-key={key}
      aria-label={label}
      aria-haspopup="dialog"
      aria-expanded={open?.key === key}
      onClick={(e) => (open?.key === key ? close() : (clearTimers(), show(key, e.currentTarget)))}
      onMouseEnter={(e) => hoverIn(key, e.currentTarget)}
      onMouseLeave={hoverOut}
      className={cn(
        'relative grid h-11 w-11 flex-none place-items-center rounded-[10px] transition-colors hover:bg-panel-2',
        (opts.current || open?.key === key) && 'bg-panel-2',
      )}
    >
      {opts.current && <span aria-hidden className="absolute -left-[10px] bottom-2.5 top-2.5 w-[3px] rounded-r bg-accent" />}
      {content}
      {opts.running && (
        <span aria-label="working" title="Working" className="absolute right-0.5 top-0.5 h-2.5 w-2.5 animate-pulse rounded-full border-2 border-panel bg-accent" />
      )}
    </button>
  );

  const agents = groups.filter((g) => g.kind === 'agent');
  const teams = groups.filter((g) => g.kind === 'team');
  const groupTile = (g: RailGroup) => tile(
    g.key,
    `${g.name}${g.kind === 'team' ? ' team' : ''}, ${g.chats.length} ${g.chats.length === 1 ? 'chat' : 'chats'}`,
    <AgentBadge name={g.name} team={g.kind === 'team'} avatar={g.avatar} size="lg" />,
    { current: g.current, running: g.chats.some((c) => c.running) },
  );

  const shownGroup = open && open.key !== PINNED ? groups.find((g) => g.key === open.key) : undefined;
  const shownChats = open?.key === PINNED ? pinned : shownGroup?.chats;

  return (
    <nav ref={railRef} aria-label="Conversations, minimized" className="flex h-full min-h-0 w-full flex-col items-center gap-1.5 py-2.5">
      <button
        type="button"
        onClick={() => onExpand()}
        aria-label="Show the full chat list"
        title="Show the full chat list"
        className="grid h-9 w-9 flex-none place-items-center rounded-lg text-fg-dim hover:bg-panel-2 hover:text-fg"
      >
        <svg aria-hidden width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m13 17 5-5-5-5M6 17l5-5-5-5" /></svg>
      </button>
      <button
        type="button"
        onClick={onNew}
        aria-label="New chat"
        title="New chat"
        className="grid h-9 w-9 flex-none place-items-center rounded-lg border border-line text-[15px] hover:border-fg-dim"
      >
        <span aria-hidden>+</span>
      </button>
      <span aria-hidden className="my-0.5 h-px w-6 flex-none bg-line" />
      <div className="flex min-h-0 w-full flex-1 flex-col items-center gap-1.5 overflow-y-auto overscroll-contain [scrollbar-width:none]" onScroll={close}>
        {pinned.length > 0 && tile(PINNED, `Pinned, ${pinned.length} ${pinned.length === 1 ? 'chat' : 'chats'}`,
          <span aria-hidden className="font-mono text-[13px] text-fg-dim">▲</span>)}
        {agents.map(groupTile)}
        {agents.length > 0 && teams.length > 0 && <span aria-hidden className="my-0.5 h-px w-6 flex-none bg-line" />}
        {teams.map(groupTile)}
      </div>
      {archivedCount > 0 && (
        <button
          type="button"
          onClick={() => onExpand('archived')}
          aria-label={`Archived, ${archivedCount} ${archivedCount === 1 ? 'chat' : 'chats'}`}
          title={`Archived (${archivedCount})`}
          className="grid h-9 w-9 flex-none place-items-center rounded-lg text-fg-dim hover:bg-panel-2 hover:text-fg"
        >
          <svg aria-hidden width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="5" rx="1" /><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" /></svg>
        </button>
      )}

      {open && shownChats && (
        <div
          ref={flyoutRef}
          role="dialog"
          aria-label={shownGroup ? `${shownGroup.name} chats` : 'Pinned chats'}
          onMouseEnter={clearTimers}
          onMouseLeave={hoverOut}
          style={{ top: open.top - 4 - shift, left: open.left, width: FLYOUT_WIDTH }}
          className="animate-rise fixed z-50 flex max-h-[min(70vh,480px)] flex-col overflow-hidden rounded-[10px] border border-line bg-panel shadow-[0_12px_32px_rgba(0,0,0,0.18)]"
        >
          <div className="flex flex-none items-center gap-2.5 border-b border-line px-3 py-2.5">
            {shownGroup
              ? <AgentBadge name={shownGroup.name} team={shownGroup.kind === 'team'} avatar={shownGroup.avatar} />
              : <span aria-hidden className="w-[22px] text-center font-mono text-[12px] text-fg-dim">▲</span>}
            <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
              {shownGroup ? shownGroup.name : 'Pinned'}
              {shownGroup?.kind === 'team' && <span className="ml-1.5 rounded-[3px] border border-line px-1 align-[1px] font-mono text-[9.5px] font-normal text-fg-dim">team</span>}
            </span>
            {shownGroup && (
              <button
                type="button"
                onClick={() => { close(); onNewWith(shownGroup); }}
                aria-label={`New chat with ${shownGroup.name}`}
                title={`New chat with ${shownGroup.name}`}
                className="grid h-6 w-6 flex-none place-items-center rounded border border-line text-[13px] text-fg-dim hover:border-fg-dim hover:text-fg"
              >
                <span aria-hidden>+</span>
              </button>
            )}
          </div>
          <ul className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            {shownChats.map((chat) => (
              <li key={chat.id}>
                <button
                  type="button"
                  aria-current={chat.selected ? 'true' : undefined}
                  onClick={() => { close(); onOpenChat(chat.id); }}
                  className={cn('flex min-h-10 w-full items-center gap-2 border-b border-line px-3 py-2 text-left text-[13px] hover:bg-panel-2', chat.selected && 'bg-panel-2')}
                >
                  <span aria-hidden className={cn('h-1.5 w-1.5 flex-none rounded-full', chat.running ? 'animate-pulse bg-accent' : 'bg-transparent')} />
                  <span className={cn('min-w-0 flex-1 truncate', chat.selected && 'font-medium')}>{chat.title}</span>
                  <span className={cn('flex-none font-mono text-[10.5px]', chat.running ? 'text-accent-ink' : 'text-fg-dim')}>{chat.running ? 'working' : chat.when}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="flex flex-none items-center justify-between px-3 py-2 font-mono text-[10.5px] text-fg-dim">
            <span>{shownChats.length} {shownChats.length === 1 ? 'chat' : 'chats'}</span>
            <button type="button" onClick={() => { const key = open.key; close(); onExpand(key === PINNED ? 'pinned' : key); }} className="hover:text-fg">
              open in full list ›
            </button>
          </div>
        </div>
      )}
    </nav>
  );
}
