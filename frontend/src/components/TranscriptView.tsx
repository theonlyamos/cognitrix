import type { TranscriptEntry } from '@/lib/transcript';
import { cn } from '@/lib/utils';

// Wide enough for real agent names ("BACKEND ENGINEER"); Home's chat keeps its
// own narrower YOU/AGENT gutter.
const ROW = 'grid grid-cols-1 gap-2 border-b border-line px-4 py-3 sm:grid-cols-[96px_1fr] sm:gap-4';
const GUTTER = 'pt-0.5 font-mono text-[11px] tracking-[0.06em] break-words';

/** Gutter label: the speaking agent's name when the entry carries one (multi-
 *  agent runs), generic AGENT otherwise (older transcripts have no name). */
const speaker = (name?: string) => (name ? name.toUpperCase() : 'AGENT');

/** Renders a parsed session transcript (chat turns, tool activity, timing
 *  rows). Shared by task run history and live monitoring. */
export function TranscriptView({ entries, live }: { entries: TranscriptEntry[]; live?: boolean }) {
  return (
    <div>
      {entries.map((e, i) => {
        switch (e.kind) {
          case 'user':
          case 'assistant': {
            const isUser = e.kind === 'user';
            const mono = !isUser && e.content.trimStart().startsWith('{');
            return (
              <div key={i} className={ROW}>
                <div className={cn(GUTTER, isUser ? 'text-accent-ink' : 'text-fg-dim')} title={isUser ? undefined : speaker(e.kind === 'assistant' ? e.name : undefined)}>
                  {isUser ? 'YOU' : speaker(e.name)}
                </div>
                <div className={cn('min-w-0 whitespace-pre-wrap break-words leading-relaxed', mono && 'font-mono text-[12px] text-fg-dim')}>
                  {e.content}
                </div>
              </div>
            );
          }
          case 'tool_calls':
            return (
              <div key={i} className={ROW}>
                <div className={cn(GUTTER, 'text-fg-dim')}>{speaker(e.name)}</div>
                <div className="min-w-0 space-y-1.5">
                  {e.content.trim() && <div className="whitespace-pre-wrap break-words leading-relaxed">{e.content}</div>}
                  {e.tools.map((t, j) => (
                    <details key={j} className="font-mono text-[11px]">
                      <summary className="cursor-pointer text-accent-ink">→ {t.name}</summary>
                      <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded border border-line bg-panel-2 p-2 text-fg-dim">{t.args}</pre>
                    </details>
                  ))}
                </div>
              </div>
            );
          case 'tool_result':
            return (
              <div key={i} className={ROW}>
                <div className={cn(GUTTER, 'text-fg-dim')}>TOOL</div>
                <details className="min-w-0 font-mono text-[11px]">
                  <summary className="cursor-pointer truncate text-fg-dim">{(e.content.split('\n')[0] || 'result').slice(0, 120)}</summary>
                  <pre className="mt-1 max-h-64 overflow-y-auto overflow-x-auto whitespace-pre-wrap break-words rounded border border-line bg-panel-2 p-2 text-fg-dim">{e.content}</pre>
                </details>
              </div>
            );
          case 'timing':
            return (
              <div key={i} className="border-b border-line px-4 py-1.5 font-mono text-[10.5px] text-fg-dim">
                <span className="opacity-70">{e.label}</span>
                {e.tokens && <span className="opacity-70"> · {e.tokens}</span>}
              </div>
            );
          case 'summary':
            return (
              <div key={i} className="border-b border-line px-4 py-3 font-mono text-[12px] text-fg-dim">
                <span className="text-accent-ink">[history compacted]</span>{' '}
                <span className="whitespace-pre-wrap break-words">{e.content}</span>
              </div>
            );
          case 'system':
          default:
            return (
              <div key={i} className="whitespace-pre-wrap break-words border-b border-line px-4 py-3 font-mono text-[12px] text-fg-dim">
                {e.content}
              </div>
            );
        }
      })}
      {live && (
        <div className="flex items-center gap-2.5 px-4 py-3 font-mono text-[12px] text-fg-dim">
          <span className="think-bars"><i /><i /><i /><i /></span>
          working…
        </div>
      )}
    </div>
  );
}
