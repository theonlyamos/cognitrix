import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown as ChevronDownIcon } from 'lucide-react';
import { AgentBadge } from '@/components/StartChat';
import { agentModel, type PickTarget, type PickerAgent, type PickerTeam } from '@/lib/agentPicker';
import { cn } from '@/lib/utils';

export const NEW_CHAT_SUGGESTIONS = [
  'What can you help me with?',
  'Plan my week',
  'Research a topic and write a short brief',
];

export function greetingFor(date: Date = new Date()): string {
  const hour = date.getHours();
  if (hour < 5) return 'Good evening';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

/**
 * A new chat: a greeting, the message box in the middle of the page, a few
 * things to try, and the agents and teams you talked to last.
 */
export function NewChatGreeting({
  name,
  agents,
  teams,
  currentAgentId,
  recent,
  onPick,
  onSuggestion,
  children,
}: {
  name?: string | null;
  agents: PickerAgent[];
  teams: PickerTeam[];
  currentAgentId: string;
  /** Agents and teams the user chatted with last, newest first. */
  recent: PickTarget[];
  onPick: (target: PickTarget) => void;
  onSuggestion: (text: string) => void;
  /** The message box. */
  children: ReactNode;
}) {
  const first = (name || '').trim().split(/\s+/)[0];
  const others = recent
    .filter((t) => !(t.kind === 'agent' && t.id === currentAgentId))
    .flatMap((t) => {
      const named = t.kind === 'agent' ? agents.find((a) => a.id === t.id) : teams.find((x) => x.id === t.id);
      return named ? [{ target: t, named }] : [];
    })
    .slice(0, 3);
  return (
    <div className="mx-auto flex min-h-full w-full max-w-[720px] flex-col justify-center px-4 pb-16 pt-10 sm:px-6">
      <div className="flex flex-col items-center text-center">
        <span aria-hidden className="new-chat-art mb-5 grid h-12 w-12 place-items-center rounded-2xl bg-accent text-accent-foreground">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
        </span>
        <h2 className="text-[28px] font-bold leading-tight tracking-[-0.015em] sm:text-[30px]">
          {greetingFor()}{first ? `, ${first}` : ''}
        </h2>
        <p className="mt-2 text-[15px] text-fg-dim">What should we work on?</p>
      </div>
      <div className="mt-6">{children}</div>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {NEW_CHAT_SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onSuggestion(s)}
            className="min-h-11 rounded-full border border-line bg-panel px-3.5 text-[13px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:min-h-9"
          >
            {s}
          </button>
        ))}
      </div>
      {others.length > 0 && (
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2 text-[13px] text-fg-dim">
          <span>Or start with</span>
          {others.map(({ target, named }) => (
            <button
              key={`${target.kind}:${target.id}`}
              type="button"
              onClick={() => onPick(target)}
              className="flex min-h-11 items-center gap-1.5 rounded-full bg-panel-2 pl-1.5 pr-3 text-fg transition-colors hover:bg-line md:min-h-8"
            >
              <AgentBadge name={named.name} team={target.kind === 'team'} avatar={target.kind === 'agent' ? (named as PickerAgent).avatar : undefined} />
              {named.name}
              {target.kind === 'team' && <span className="text-fg-dim">(team)</span>}
            </button>
          ))}
          <Link to="/agents" className="min-h-11 content-center px-1 underline-offset-2 hover:text-fg hover:underline md:min-h-8">All agents</Link>
        </div>
      )}
    </div>
  );
}

/** The agent chip in a new chat's message box: who this chat will be with. */
export function AgentPickerChip({
  agents,
  teams,
  currentAgentId,
  onPick,
}: {
  agents: PickerAgent[];
  teams: PickerTeam[];
  currentAgentId: string;
  onPick: (target: PickTarget) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const current = agents.find((a) => a.id === currentAgentId);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!current) return null;
  const pick = (target: PickTarget) => {
    setOpen(false);
    onPick(target);
  };
  const item = (target: PickTarget, name: string, line: string, avatar?: PickerAgent['avatar']) => (
    <button
      key={`${target.kind}:${target.id}`}
      type="button"
      role="menuitemradio"
      aria-checked={target.kind === 'agent' && target.id === currentAgentId}
      onClick={() => pick(target)}
      className={cn(
        'flex min-h-11 w-full items-center gap-2.5 rounded-md px-2.5 text-left transition-colors hover:bg-panel-2 md:min-h-9',
        target.kind === 'agent' && target.id === currentAgentId && 'bg-panel-2',
      )}
    >
      <AgentBadge name={name} team={target.kind === 'team'} avatar={avatar} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] text-fg">{name}</span>
        {line && <span className="block truncate text-[11px] text-fg-dim">{line}</span>}
      </span>
    </button>
  );

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Chat with ${current.name}. Change`}
        onClick={() => setOpen((v) => !v)}
        className="flex h-11 items-center gap-1.5 rounded-full border border-line pl-1.5 pr-2 text-[12.5px] text-fg transition-colors hover:border-fg-dim md:h-8"
      >
        <AgentBadge name={current.name} avatar={current.avatar} />
        <span className="max-w-[140px] truncate">{current.name}</span>
        <ChevronDownIcon className="h-3.5 w-3.5 text-fg-dim" strokeWidth={2} aria-hidden="true" />
      </button>
      {open && (
        <div role="menu" aria-label="Chat with" className="animate-rise absolute bottom-full left-0 z-30 mb-2 max-h-[min(60vh,420px)] w-[280px] overflow-y-auto rounded-xl border border-line bg-panel p-1.5 shadow-lg">
          <div className="px-2.5 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-dim">Agents</div>
          {agents.map((a) => item({ kind: 'agent', id: a.id }, a.name, agentModel(a), a.avatar))}
          {teams.length > 0 && (
            <>
              <div className="px-2.5 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-dim">Teams</div>
              {teams.map((t) => item({ kind: 'team', id: t.id }, t.name, 'group chat'))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
