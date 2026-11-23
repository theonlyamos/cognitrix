import { useState } from 'react';
import { AppWindow, FileText, SquareStack } from 'lucide-react';
import { turnTime, visibleMarks, type ChillTurn } from '@/lib/chillTurns';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

function MadeIcon({ made, size = 11 }: { made: NonNullable<ChillTurn['made']>; size?: number }) {
  if (made === 'page') return <SquareStack size={size} aria-hidden />;
  if (made === 'app') return <AppWindow size={size} aria-hidden />;
  return <FileText size={size} aria-hidden />;
}

interface Props {
  turns: ChillTurn[];
  current: number;
  /** The newest turn is still being answered. */
  replying: boolean;
  onPick: (index: number) => void;
}

/**
 * A short line per turn down the right edge. The turn on screen is the long
 * accent line; turns that made a page or file carry its icon. Hover a line to
 * see the question, click it to go there.
 */
export function ChillTurnMarks({ turns, current, replying, onPick }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const { from, to } = visibleMarks(turns.length, current);
  const last = turns.length - 1;
  return (
    <nav aria-label={tr('ui.chill.turns')} className="pointer-events-none absolute inset-y-0 right-0 z-20 flex items-center"
      onMouseLeave={() => setHover(null)}>
      <div className="pointer-events-auto flex flex-col items-end py-2 pl-2 pr-1 sm:pr-3">
        <span className="mb-1.5 hidden pr-1 font-mono text-[10.5px] tracking-wide text-fg-dim md:block" aria-hidden>
          {current + 1} / {turns.length}
        </span>
        {from > 0 && <span aria-hidden className="pr-1 text-[10px] leading-3 text-fg-dim">+{from}</span>}
        <ol className="flex flex-col items-end">
          {turns.slice(from, to).map((turn, offset) => {
            const index = from + offset;
            const on = index === current;
            const time = turnTime(turn.at);
            const label = tr('ui.chill.turnNOfM', { n: index + 1, m: turns.length });
            return (
              <li key={turn.key} className="relative">
                <button type="button" onClick={() => { setHover(null); onPick(index); }} aria-current={on ? 'true' : undefined}
                  aria-label={`${label}: ${turn.question}`}
                  onMouseEnter={() => setHover(index)} onFocus={() => setHover(index)} onBlur={() => setHover(null)}
                  className="group flex h-[22px] w-7 items-center justify-end gap-1.5 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent md:h-[17px] md:w-11">
                  {turn.made && (
                    <span className={cn('hidden text-fg-dim md:block', on && 'text-fg')}><MadeIcon made={turn.made} /></span>
                  )}
                  <i aria-hidden className={cn(
                    'block rounded-sm transition-all',
                    on ? 'h-1 w-4 bg-accent-ink md:w-[30px]'
                      : cn('h-[3px] w-2 bg-fg/25 group-hover:bg-fg/60 md:w-3.5', turn.made && 'md:w-5'),
                    replying && index === last && !on && 'bg-accent shadow-[0_0_0_4px_rgb(var(--accent-rgb)/.25)] motion-safe:animate-pulse',
                  )} />
                </button>
                {hover === index && (
                  <div role="tooltip" className="pointer-events-none absolute right-full top-1/2 mr-2 hidden w-[270px] -translate-y-1/2 rounded-xl border border-line bg-panel px-3 py-2.5 text-[13px] leading-snug shadow-lg md:block">
                    <div className="mb-0.5 font-mono text-[10.5px] uppercase tracking-wide text-fg-dim">
                      {label}{time && ` · ${time}`}
                    </div>
                    <div className="line-clamp-3">{turn.question || tr('ui.chill.noQuestion')}</div>
                    {turn.made && turn.madeName && (
                      <div className="mt-1.5 flex items-center gap-1.5 text-[12px] text-fg-dim">
                        <MadeIcon made={turn.made} size={12} />
                        <span className="truncate">{tr('ui.chill.madeName', { name: turn.madeName })}</span>
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
        {to < turns.length && <span aria-hidden className="pr-1 text-[10px] leading-3 text-fg-dim">+{turns.length - to}</span>}
      </div>
    </nav>
  );
}
