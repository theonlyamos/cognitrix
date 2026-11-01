import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { agentModel, teamLine, type PickTarget, type PickerAgent, type PickerTeam } from '@/lib/agentPicker';

/** Square letter badge used for agents and teams (teams get a dashed edge). */
export function AgentBadge({
  name,
  team = false,
  selected = false,
  size = 'md',
  className,
}: {
  name: string;
  team?: boolean;
  selected?: boolean;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}) {
  const letter = (name.trim()[0] || '?').toUpperCase();
  return (
    <span
      aria-hidden
      className={cn(
        'inline-grid flex-none place-items-center border font-mono font-medium',
        size === 'sm' && 'h-[18px] w-[18px] rounded-[4px] text-[9.5px]',
        size === 'md' && 'h-[22px] w-[22px] rounded-[5px] text-[11px]',
        size === 'lg' && 'h-9 w-9 rounded-lg text-[15px]',
        team && 'border-dashed',
        className,
        selected ? 'border-transparent bg-accent text-accent-foreground' : 'border-line bg-panel-2 text-fg',
      )}
    >
      {letter}
    </span>
  );
}

/** "Who do you want to talk to?" screen shown for a new chat. */
export function StartChat({
  agents,
  teams,
  recent = [],
  onPick,
}: {
  agents: PickerAgent[];
  teams: PickerTeam[];
  /** Agents and teams the user chatted with last, newest first. */
  recent?: PickTarget[];
  onPick: (target: PickTarget) => void;
}) {
  const card = (target: PickTarget, name: string, line: string, desc: string | null | undefined) => (
    <button
      key={`${target.kind}:${target.id}`}
      type="button"
      onClick={() => onPick(target)}
      className="flex flex-col gap-2.5 rounded-lg border border-line bg-panel p-3.5 text-left transition-colors hover:border-fg-dim"
    >
      <span className="flex min-w-0 items-center gap-2.5">
        <AgentBadge name={name} team={target.kind === 'team'} size="lg" />
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-[14.5px] font-medium">{name}</span>
          {line && <span className="truncate font-mono text-[10.5px] text-fg-dim">{line}</span>}
        </span>
      </span>
      {desc && <span className="line-clamp-2 text-[12.5px] leading-snug text-fg-dim">{desc}</span>}
    </button>
  );
  const addCard = 'flex min-h-[4.25rem] items-center justify-center rounded-lg border border-dashed border-line p-3.5 text-[13px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg';
  const recentCards = recent.slice(0, 3).flatMap((target) => {
    if (target.kind === 'agent') {
      const a = agents.find((x) => x.id === target.id);
      return a ? [card(target, a.name, agentModel(a), a.description)] : [];
    }
    const t = teams.find((x) => x.id === target.id);
    return t ? [card(target, t.name, teamLine(t), t.description)] : [];
  });
  const heading = 'font-mono text-[10px] uppercase tracking-[0.14em] text-fg-dim';
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col px-4 py-10 sm:px-6">
      <p className="font-mono text-[12px] tracking-[0.04em] text-accent-ink">&gt;_ new chat</p>
      <h2 className="mt-3 text-2xl font-bold tracking-tight">Who do you want to talk to?</h2>
      <p className="mt-2 text-fg-dim">Each chat is with one agent or team. You can start another chat with someone else at any time.</p>
      {recentCards.length > 0 && (
        <>
          <h3 className={cn(heading, 'mb-2 mt-7')}>Recent</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">{recentCards}</div>
        </>
      )}
      <h3 className={cn(heading, 'mb-2', recentCards.length > 0 ? 'mt-6' : 'mt-7')}>Agents</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {agents.map((a) => card({ kind: 'agent', id: a.id }, a.name, agentModel(a), a.description))}
        <Link to="/agents/new" className={addCard}>+ New agent</Link>
      </div>
      <h3 className={cn(heading, 'mb-2 mt-6')}>Teams</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {teams.map((t) => card({ kind: 'team', id: t.id }, t.name, teamLine(t), t.description))}
        <Link to="/teams/new" className={addCard}>+ New team</Link>
      </div>
    </div>
  );
}
