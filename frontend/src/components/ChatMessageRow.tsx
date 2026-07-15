import { lazy, memo, Suspense } from 'react';
import { ArtifactPreview } from '@/components/ArtifactPreview';
import type { ChatMessage } from '@/context/SessionContext';
import { cn } from '@/lib/utils';

const MarkdownMessage = lazy(() => import('@/components/MarkdownMessage'));

const humanizeTool = (name: string) => name.replace(/_/g, ' ');

const prettyJson = (value: string) => {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
};

const fmtTime = (value?: string | number) =>
  value ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '';

interface ChatMessageRowProps {
  message: ChatMessage;
  isLast: boolean;
  streaming: boolean;
}

export const ChatMessageRow = memo(function ChatMessageRow({ message, isLast, streaming }: ChatMessageRowProps) {
  if (message.role === 'tool') {
    if (!message.tools?.length) return null;
    return (
      <div className="grid grid-cols-1 gap-2 border-b border-line px-4 py-3 sm:grid-cols-[68px_1fr] sm:gap-4 sm:px-6">
        <div className="pt-0.5 font-mono text-[11px] tracking-[0.06em] text-fg-dim">TOOL</div>
        <div className="flex flex-col items-start gap-1.5">
          {message.tools.map((tool, index) => (
            <div key={tool.id || index} className="w-full max-w-2xl space-y-2">
              <details className="group w-full">
              <summary className="inline-flex min-h-11 list-none cursor-pointer select-none items-center gap-1.5 rounded border border-line bg-panel-2 px-2 py-1 font-mono text-[11px] transition-colors hover:border-fg-dim sm:min-h-0 [&::-webkit-details-marker]:hidden">
                {tool.status === 'running' ? (
                  <span className="think-bars"><i /><i /><i /></span>
                ) : tool.status === 'error' ? (
                  <span className="text-danger-ink" aria-hidden>✗</span>
                ) : tool.status === 'stopped' ? (
                  <span className="text-fg-dim" aria-hidden>■</span>
                ) : (
                  <span className="text-accent-ink" aria-hidden>✓</span>
                )}
                <span className={cn(tool.status === 'running' ? 'text-accent-ink' : 'text-fg')}>
                  {humanizeTool(tool.name)}
                </span>
                {tool.status === 'running' && <span className="text-fg-dim">running…</span>}
                {tool.status === 'done' && <span className="sr-only">Completed</span>}
                {tool.status === 'error' && <span className="sr-only">Failed</span>}
                {tool.status === 'stopped' && <span className="sr-only">Stopped</span>}
                <svg className="ml-0.5 text-fg-dim transition-transform group-open:rotate-90" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M9 6l6 6-6 6" /></svg>
              </summary>
              <div className="mt-1.5 space-y-2 rounded border border-line bg-panel-2 p-2 font-mono text-[11px]">
                {tool.params && tool.params !== '{}' && (
                  <div>
                    <div className="mb-1 text-[10px] uppercase tracking-wider text-fg-dim">params</div>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-fg-dim">{prettyJson(tool.params)}</pre>
                  </div>
                )}
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-fg-dim">result</div>
                  {tool.status === 'running' ? (
                    <span className="text-fg-dim">running…</span>
                  ) : (
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-fg-dim">{tool.result || '(no output)'}</pre>
                  )}
                </div>
              </div>
              </details>
              {tool.artifacts?.map((artifact) => <ArtifactPreview key={artifact.id} artifact={artifact} />)}
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (message.role === 'assistant' && !message.content.trim() && !(isLast && streaming)) return null;

  const isUser = message.role === 'user';
  return (
    <div className="grid grid-cols-1 gap-2 border-b border-line px-4 py-4 sm:grid-cols-[68px_1fr] sm:gap-4 sm:px-6">
      <div className={cn('pt-0.5 font-mono text-[11px] tracking-[0.06em]', isUser ? 'text-accent-ink' : 'text-fg-dim')}>
        {isUser ? 'YOU' : 'AGENT'}
      </div>
      <div className="min-w-0" aria-live={!isUser && isLast && streaming ? 'off' : undefined}>
        {isUser ? (
          <div className="whitespace-pre-wrap break-words leading-relaxed">{message.content}</div>
        ) : isLast && streaming ? (
          <div className="whitespace-pre-wrap break-words leading-relaxed">
            {message.content}
            <span className="caret" />
          </div>
        ) : (
          <div className="md break-words">
            <Suspense fallback={<div className="whitespace-pre-wrap break-words leading-relaxed">{message.content}</div>}>
              <MarkdownMessage content={message.content} />
            </Suspense>
          </div>
        )}
        {message.timestamp && (
          <div className="mt-2 font-mono text-[10.5px] text-fg-dim">
            <span className="opacity-70">at</span> {fmtTime(message.timestamp)}
          </div>
        )}
      </div>
    </div>
  );
});
