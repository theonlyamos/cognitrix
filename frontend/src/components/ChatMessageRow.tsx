import { lazy, memo, Suspense, useState } from 'react';
import { ArtifactPreview } from '@/components/ArtifactPreview';
import { ArtifactCard } from '@/components/artifacts/ArtifactCard';
import { AppCard } from '@/components/hosted-apps/AppCard';
import { MemoryNotice } from '@/components/MemoryNotice';
import type { ChatMessage, ToolArtifact } from '@/context/SessionContext';
import {
  BlockedEndpointNotice, RefusedNotice, SignInNeededNotice, blockedEndpoint, isRefusal, signInNeeded,
} from '@/components/SafetyNotices';
import { speakText, stopSpeaking, useSpeechState } from '@/lib/speech';
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
  onEditSource?: (artifact: ToolArtifact) => void;
  selectedEditSourceId?: string;
  /** Reword this message and send it again (the person's own messages). */
  onEdit?: (message: ChatMessage) => void;
  /** Ask for a new answer to the last question (the latest reply only). */
  onRegenerate?: () => void;
}

const actionClass =
  'min-h-11 px-1 font-mono text-[10.5px] text-fg-dim transition-colors hover:text-fg md:min-h-0';

/** Reasoning the model streamed inside <think> tags is not part of the answer. */
const withoutThinking = (text: string) => text.replace(/<think>[\s\S]*?<\/think>/g, '').trim();

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={actionClass}
      onClick={() => {
        void navigator.clipboard?.writeText(withoutThinking(text)).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        }).catch(() => {});
      }}
    >
      {copied ? 'copied' : 'copy'}
    </button>
  );
}

/** Read a reply aloud, or stop reading it. */
function ListenButton({ id, text }: { id: string; text: string }) {
  const speech = useSpeechState();
  const mine = speech.id === id;
  const active = mine && speech.phase !== 'idle';
  return (
    <>
      <button
        type="button"
        className={actionClass}
        aria-pressed={active}
        onClick={() => (active ? stopSpeaking() : void speakText(id, text))}
      >
        {active ? (speech.phase === 'loading' ? 'loading… stop' : 'stop') : 'listen'}
      </button>
      {mine && speech.error && <span role="alert" className="font-mono text-[10.5px] text-danger-ink">{speech.error}</span>}
    </>
  );
}

export const ChatMessageRow = memo(function ChatMessageRow({
  message,
  isLast,
  streaming,
  onEditSource,
  selectedEditSourceId,
  onEdit,
  onRegenerate,
}: ChatMessageRowProps) {
  if (message.role === 'tool') {
    if (!message.tools?.length) return null;
    return (
      <div className="grid grid-cols-1 gap-2 border-b border-line px-4 py-3 sm:grid-cols-[68px_1fr] sm:gap-4 sm:px-6">
        <div className="pt-0.5 font-mono text-[11px] tracking-[0.06em] text-fg-dim">TOOL</div>
        <div className="flex min-w-0 flex-col items-start gap-1.5">
          {message.tools.map((tool, index) => (
            <div key={tool.id || index} className="w-full max-w-2xl space-y-2">
              <details className="group w-full">
              <summary className="relative inline-flex min-h-11 list-none cursor-pointer select-none items-center gap-1.5 rounded border border-line bg-panel-2 px-2 py-1 font-mono text-[11px] transition-colors hover:border-fg-dim sm:min-h-0 [&::-webkit-details-marker]:hidden">
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
              {tool.status !== 'running' && isRefusal(tool.result) && <RefusedNotice result={tool.result} />}
              {tool.status !== 'running' && signInNeeded(tool.result) && <SignInNeededNotice app={signInNeeded(tool.result)!} />}
              {tool.status === 'done' && <MemoryNotice toolName={tool.name} result={tool.result} />}
              {tool.status === 'done' && <ArtifactCard toolName={tool.name} result={tool.result} />}
              {tool.status === 'done' && <AppCard toolName={tool.name} result={tool.result} />}
              {tool.artifacts?.map((artifact) => (
                <ArtifactPreview
                  key={artifact.id}
                  artifact={artifact}
                  onEditSource={onEditSource}
                  selected={selectedEditSourceId === artifact.id}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (message.role === 'assistant' && !message.content.trim() && !(isLast && streaming)) return null;

  const isUser = message.role === 'user';
  const blockedUrl = isUser ? null : blockedEndpoint(message.content);
  return (
    <div className="group/msg grid grid-cols-1 gap-2 border-b border-line px-4 py-4 sm:grid-cols-[68px_1fr] sm:gap-4 sm:px-6">
      <div
        className={cn('truncate pt-0.5 font-mono text-[11px] tracking-[0.06em]', isUser ? 'text-accent-ink' : 'text-fg-dim')}
        title={message.speaker}
      >
        {isUser ? 'YOU' : message.speaker ? message.speaker.toUpperCase() : 'AGENT'}
      </div>
      <div className="min-w-0" aria-live={!isUser && isLast && streaming ? 'off' : undefined}>
        {blockedUrl && <BlockedEndpointNotice url={blockedUrl} />}
        {isUser ? (
          <>
            <div className="whitespace-pre-wrap break-words leading-relaxed">{message.content}</div>
            {message.steered && (
              <div className="mt-1 font-mono text-[10.5px] text-fg-dim">sent while the agent was working</div>
            )}
          </>
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
        {isUser && message.artifacts?.map((artifact) => (
          <div key={artifact.id} className="mt-3">
            <ArtifactPreview
              artifact={artifact}
              onEditSource={onEditSource}
              selected={selectedEditSourceId === artifact.id}
            />
          </div>
        ))}
        {message.timestamp && (
          <div className="mt-2 font-mono text-[10.5px] text-fg-dim">
            <span className="opacity-70">at</span> {fmtTime(message.timestamp)}
          </div>
        )}
        {message.content.trim() && !(isLast && streaming) && (
          <div className="mt-1 flex flex-wrap items-center gap-3 opacity-100 transition-opacity md:opacity-0 md:group-hover/msg:opacity-100 md:group-focus-within/msg:opacity-100">
            <CopyButton text={message.content} />
            {!isUser && <ListenButton id={message.id} text={message.content} />}
            {isUser && onEdit && (
              <button type="button" className={actionClass} onClick={() => onEdit(message)}>edit</button>
            )}
            {!isUser && onRegenerate && (
              <button type="button" className={actionClass} onClick={onRegenerate}>regenerate</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
});
