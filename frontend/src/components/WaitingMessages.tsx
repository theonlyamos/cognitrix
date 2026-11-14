export interface WaitingMessage {
  id: string;
  content: string;
  source?: string;
}

interface Props {
  messages: WaitingMessage[];
  /** Stop the current step; waiting messages are sent right after. */
  onSendNow: () => void;
  onEdit: (message: WaitingMessage) => void;
  onRemove: (message: WaitingMessage) => void;
  sendingNow?: boolean;
}

/**
 * Messages sent while the agent is working. They wait at the end of the chat
 * until the agent reads them after its current step (or, if it finishes
 * first, they start the next turn).
 */
export function WaitingMessages({ messages, onSendNow, onEdit, onRemove, sendingNow }: Props) {
  if (!messages.length) return null;
  const link = 'min-h-11 rounded px-1 underline decoration-line underline-offset-2 transition-colors hover:text-fg md:min-h-0';
  return (
    <div aria-label="Messages waiting for the agent">
      {messages.map((message) => (
        <div
          key={message.id}
          className="grid grid-cols-1 gap-2 border-b border-line px-4 py-3 sm:grid-cols-[68px_1fr] sm:gap-4 sm:px-6"
        >
          <div className="pt-0.5 font-mono text-[11px] tracking-[0.06em] text-accent-ink">YOU</div>
          <div className="min-w-0">
            <div className="whitespace-pre-wrap break-words rounded border border-dashed border-fg-dim/50 px-3 py-2 leading-relaxed text-fg-dim">
              {message.content}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-fg-dim">
              <span className="text-accent-ink">
                waiting<span className="hidden sm:inline"> · read after this step</span>
                {message.source && message.source !== 'web' ? ` · from ${message.source}` : ''}
              </span>
              <button type="button" onClick={onSendNow} disabled={sendingNow} className={`${link} text-fg disabled:opacity-60`}>
                {sendingNow ? 'sending…' : 'send now'}
              </button>
              <button type="button" onClick={() => onEdit(message)} className={link}>edit</button>
              <button type="button" onClick={() => onRemove(message)} className={link}>remove</button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
