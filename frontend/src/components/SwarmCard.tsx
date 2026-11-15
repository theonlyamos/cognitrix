import { useState } from 'react';
import { cn } from '@/lib/utils';
import { ENDED, progressLabel, tokensLabel, type Swarm, type SwarmWorker } from '@/lib/swarms';

interface Props {
  swarm: Swarm;
  /** Narrow layout for the chat sidebar. */
  compact?: boolean;
  onStop: (swarmId: string) => void | Promise<void>;
  onStopWorker: (swarmId: string, name: string) => void | Promise<void>;
  onMerge?: (swarmId: string, name: string) => Promise<string>;
  onOpenChat?: (chatId: string) => void;
}

const MARK: Record<string, string> = { queued: '○', running: '✱', reviewing: '✱', done: '✓', failed: '×', stopped: '■' };
const WORD: Record<string, string> = {
  queued: 'waiting', running: 'working', reviewing: 'being checked', done: 'done', failed: 'failed', stopped: 'stopped',
};

function detail(w: SwarmWorker): string {
  if (w.status === 'failed') return w.error || 'It failed.';
  if (w.status === 'stopped') return w.error || 'Stopped.';
  if (w.status === 'done') return w.summary || 'Done.';
  if (w.status === 'queued') return w.depends_on.length ? `Waits for ${w.depends_on.join(', ')}` : 'Waiting for a free slot';
  if (w.stalled) return 'Nothing new for a while. It may be stuck.';
  return w.last_step ? `Using ${w.last_step}` : w.brief;
}

/** A swarm as the person sees it: how far along it is, and one row per worker. */
export function SwarmCard({ swarm, compact = false, onStop, onStopWorker, onMerge, onOpenChat }: Props) {
  const running = swarm.status === 'running';
  const [open, setOpen] = useState(running || compact);
  const [busy, setBusy] = useState('');
  const [note, setNote] = useState('');
  const l = swarm.ledger;
  const ended = l.done + l.failed + l.stopped;
  const percent = l.total ? Math.round((ended / l.total) * 100) : 0;

  const act = async (key: string, fn: () => void | Promise<unknown>) => {
    setBusy(key);
    setNote('');
    try {
      const result = await fn();
      if (typeof result === 'string') setNote(result);
    } catch {
      setNote("That didn't work. Try again.");
    } finally {
      setBusy('');
    }
  };

  return (
    <section
      aria-label={`Swarm ${swarm.title}`}
      className={cn('rounded-xl border border-line bg-panel text-[13px]', compact ? 'px-2.5 py-2.5' : 'px-3.5 py-3')}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="flex min-h-11 min-w-0 flex-1 basis-[160px] items-center gap-2 text-left md:min-h-0"
        >
          <span aria-hidden className={cn('w-3 flex-none text-center text-[8px] text-fg-dim transition-transform', !open && '-rotate-90')}>▼</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium">{swarm.title}</span>
            <span className="block truncate font-mono text-[10.5px] text-fg-dim">
              {swarm.mode === 'best_of' ? 'best of ' + l.total : 'swarm'} · {swarm.status === 'stopped' ? 'stopped' : running ? 'running' : 'finished'} · {progressLabel(swarm)} · {tokensLabel(l.tokens)} tokens
            </span>
          </span>
        </button>
        {running && (
          <button
            type="button"
            disabled={busy === 'stop'}
            onClick={() => void act('stop', () => onStop(swarm.id))}
            className={cn(button, 'border-danger/40 text-danger-ink hover:bg-danger/10')}
          >
            Stop all
          </button>
        )}
      </div>
      <div
        role="progressbar"
        aria-label="Workers finished"
        aria-valuemin={0}
        aria-valuemax={l.total}
        aria-valuenow={ended}
        className="mt-2 h-1 overflow-hidden rounded-full bg-panel-2"
      >
        <div className={cn('h-full rounded-full transition-all', swarm.status === 'stopped' ? 'bg-fg-dim' : 'bg-accent')} style={{ width: `${percent}%` }} />
      </div>
      {open && (
        <ul className="mt-2.5 flex flex-col gap-1.5">
          {swarm.workers.map((w) => {
            const live = !ENDED.includes(w.status);
            const winner = swarm.judge?.winner === w.name;
            return (
              <li key={w.id} className="flex items-start gap-2">
                <span
                  aria-hidden
                  className={cn(
                    'w-3 flex-none pt-0.5 text-center font-mono text-[11px]',
                    w.status === 'failed' ? 'text-danger-ink' : w.status === 'done' || live ? 'text-accent-ink' : 'text-fg-dim',
                    (w.status === 'running' || w.status === 'reviewing') && 'motion-safe:animate-pulse',
                  )}
                >
                  {MARK[w.status] ?? '·'}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-1.5">
                    <span className="font-medium [overflow-wrap:anywhere]">{w.name}</span>
                    <span className="font-mono text-[10.5px] text-fg-dim">
                      {[w.role || w.agent_name, w.model, WORD[w.status], w.tokens ? `${tokensLabel(w.tokens)} tokens` : ''].filter(Boolean).join(' · ')}
                    </span>
                    {winner && <span className="rounded-[3px] bg-accent px-1 font-mono text-[9.5px] text-accent-foreground">PICKED</span>}
                  </div>
                  <div className={cn('text-[12px] leading-snug text-fg-dim [overflow-wrap:anywhere]', compact ? 'line-clamp-2' : 'line-clamp-3')}>{detail(w)}</div>
                  <div className="mt-1 flex flex-wrap gap-1.5 empty:hidden">
                    {w.chat_id && onOpenChat && (
                      <button type="button" onClick={() => onOpenChat(w.chat_id)} className={button}>Open its chat</button>
                    )}
                    {live && (
                      <button type="button" disabled={busy === w.name} onClick={() => void act(w.name, () => onStopWorker(swarm.id, w.name))} className={button}>Stop</button>
                    )}
                    {w.status === 'done' && w.branch && w.changed > 0 && !w.merged && onMerge && (
                      <button type="button" disabled={busy === w.name} onClick={() => void act(w.name, () => onMerge(swarm.id, w.name))} className={button}>
                        Merge {w.changed} changed file{w.changed === 1 ? '' : 's'}
                      </button>
                    )}
                    {w.merged && <span className="font-mono text-[10.5px] text-fg-dim">merged</span>}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {open && swarm.judge?.verdict && <p className="mt-2 text-[12px] leading-snug text-fg-dim">{swarm.judge.verdict}</p>}
      {swarm.status === 'stopped' && swarm.stop_reason && <p className="mt-2 text-[12px] text-fg-dim">{swarm.stop_reason}</p>}
      {note && <p role="status" className="mt-2 text-[12px] text-fg-dim">{note}</p>}
    </section>
  );
}

const button = 'inline-flex min-h-11 items-center rounded-md border border-line px-2.5 text-[12px] hover:bg-panel-2 disabled:opacity-50 md:min-h-7';
