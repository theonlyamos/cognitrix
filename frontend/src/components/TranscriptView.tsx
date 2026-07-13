import { lazy, Suspense } from 'react';
import type { TranscriptEntry } from '@/lib/transcript';
import { cn } from '@/lib/utils';

const MarkdownMessage = lazy(() => import('@/components/MarkdownMessage'));

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
    <div role="log" aria-live="polite" aria-relevant="additions text">
      {entries.map((e, i) => {
        switch (e.kind) {
          case 'user':
          case 'assistant': {
            const isUser = e.kind === 'user';
            return (
              <div key={i} className={ROW}>
                <div className={cn(GUTTER, isUser ? 'text-accent-ink' : 'text-fg-dim')} title={isUser ? undefined : speaker(e.kind === 'assistant' ? e.name : undefined)}>
                  {isUser ? 'YOU' : speaker(e.name)}
                </div>
                <div className="min-w-0">
                  {isUser ? (
                    <div className="whitespace-pre-wrap break-words leading-relaxed">
                      {e.content}
                    </div>
                  ) : e.live ? (
                    <div className="whitespace-pre-wrap break-words leading-relaxed">
                      {e.content}
                      <span className="caret" />
                    </div>
                  ) : (
                    <div className="md break-words">
                      <Suspense fallback={
                        <div className="whitespace-pre-wrap break-words leading-relaxed">
                          {e.content}
                        </div>
                      }>
                        <MarkdownMessage content={e.content} />
                      </Suspense>
                    </div>
                  )}
                </div>
              </div>
            );
          }
          case 'tool_calls':
            return (
              <div key={i} className={ROW}>
                <div className={cn(GUTTER, 'text-fg-dim')}>{speaker(e.name)}</div>
                <div className="min-w-0 space-y-1.5">
                  {e.content.trim() && (
                    <div className="whitespace-pre-wrap break-words leading-relaxed">
                      {e.content}
                    </div>
                  )}
                  {e.tools.map((tool, toolIndex) => (
                    <details
                      key={tool.id || toolIndex}
                      className="w-full max-w-2xl font-mono text-[11px]"
                    >
                      <summary className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 text-accent-ink sm:min-h-0">
                        {tool.status === 'running' ? (
                          <span className="think-bars"><i /><i /><i /></span>
                        ) : tool.status === 'error' ? (
                          <span className="text-danger-ink" aria-hidden>✕</span>
                        ) : (
                          <span className="text-ok" aria-hidden>✓</span>
                        )}
                        <span>{tool.name.replace(/_/g, ' ')}</span>
                        {tool.status === 'running' && (
                          <span className="text-fg-dim">running…</span>
                        )}
                      </summary>
                      <div className="mt-1 space-y-2 rounded border border-line bg-panel-2 p-2">
                        {tool.args && tool.args !== '{}' && (
                          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-fg-dim">
                            {tool.args}
                          </pre>
                        )}
                        {tool.status !== 'running' && (
                          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-fg-dim">
                            {tool.result || '(no output)'}
                          </pre>
                        )}
                      </div>
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
