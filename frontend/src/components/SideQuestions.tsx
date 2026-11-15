import { useCallback, useEffect, useRef, useState } from 'react';
import MarkdownMessage from '@/components/MarkdownMessage';
import { errorMessage } from '@/lib/api';
import { askSideQuestion, clearSideQuestions, listSideQuestions, type SideQuestion } from '@/lib/sideQuestions';
import { cn } from '@/lib/utils';

const BubbleIcon = () => (
  <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />
    <path d="M9.5 9.5a2.5 2.5 0 1 1 3.3 2.4c-.5.2-.8.6-.8 1.1v.5M12 16.5h.01" />
  </svg>
);

/** Opens the side questions panel; sits in the chat box's bottom row. */
export function SideQuestionButton({ onClick, active, className }: { onClick: () => void; active?: boolean; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      aria-label="Side question"
      title="Side question: ask about this chat without interrupting it (Ctrl+/)"
      className={cn(
        'grid h-11 w-11 place-items-center rounded text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:h-8 md:w-8',
        active && 'bg-panel-2 text-fg',
        className,
      )}
    >
      <BubbleIcon />
    </button>
  );
}

interface Pending {
  question: string;
}

/**
 * Ask a quick question about the chat on screen, answered on the side by the
 * agent's model. The running job isn't steered, stopped or told about it, and
 * nothing here is added to the conversation.
 */
export function SideQuestionsPanel({ sessionId, busy, agentName, onClose, onUseInChat }: {
  sessionId: string;
  busy: boolean;
  agentName: string;
  onClose: () => void;
  /** Puts text in the main chat box (nothing is sent). */
  onUseInChat: (text: string) => void;
}) {
  const [items, setItems] = useState<SideQuestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<Pending | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setItems([]);
    setError(null);
    listSideQuestions(sessionId)
      .then((rows) => { if (live) setItems(rows); })
      .catch((err) => { if (live) setError(errorMessage(err, 'Could not load side questions.')); })
      .finally(() => { if (live) setLoading(false); });
    inputRef.current?.focus();
    return () => { live = false; };
  }, [sessionId]);

  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [items, pending]);

  const ask = useCallback(async () => {
    const question = draft.trim();
    if (!question || pending) return;
    setPending({ question });
    setDraft('');
    setError(null);
    try {
      const row = await askSideQuestion(sessionId, question);
      setItems((prev) => [...prev, row]);
    } catch (err) {
      setError(errorMessage(err, 'Could not ask that.'));
      setDraft(question);
    } finally {
      setPending(null);
      inputRef.current?.focus();
    }
  }, [draft, pending, sessionId]);

  const clear = useCallback(async () => {
    try {
      await clearSideQuestions(sessionId);
      setItems([]);
    } catch (err) {
      setError(errorMessage(err, 'Could not clear them.'));
    }
  }, [sessionId]);

  return (
    <section
      aria-label="Side questions"
      className="flex max-h-[min(70vh,560px)] w-full flex-col overflow-hidden rounded-xl border border-line bg-panel shadow-lg"
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } }}
    >
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="text-fg-dim"><BubbleIcon /></span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-fg">Side questions</div>
          <div className="truncate text-[11.5px] text-fg-dim">
            {busy ? `${agentName || 'The agent'} keeps working; this doesn’t interrupt it.` : 'Answered on the side; not added to the chat.'}
          </div>
        </div>
        {items.length > 0 && (
          <button type="button" onClick={() => void clear()} className="min-h-11 rounded px-2 text-[11.5px] text-fg-dim hover:text-fg md:min-h-0">
            Clear
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close side questions"
          className="grid h-11 w-11 place-items-center rounded text-fg-dim hover:bg-panel-2 hover:text-fg md:h-7 md:w-7"
        >
          <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </header>
      <div ref={listRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3" aria-live="polite">
        {loading && <div className="text-[12px] text-fg-dim">Loading…</div>}
        {!loading && items.length === 0 && !pending && (
          <div className="text-[12px] leading-relaxed text-fg-dim">
            Ask about what’s happening, like “what is it doing right now?” or “which file did it change?”. The answer
            comes from the conversation and the agent’s progress so far; nothing is run.
          </div>
        )}
        {items.map((q) => (
          <div key={q.id} className="space-y-1.5">
            <div className="ml-8 rounded-lg bg-panel-2 px-3 py-2 text-[13px] text-fg">{q.question}</div>
            {q.error ? (
              <div role="alert" className="text-[12px] text-danger-ink">{q.error}</div>
            ) : (
              <div className="text-[13px] text-fg">
                <MarkdownMessage content={q.answer} />
                <div className="mt-1 flex items-center gap-3 font-mono text-[10.5px] text-fg-dim">
                  {q.model && <span>{q.model}</span>}
                  <button type="button" onClick={() => void navigator.clipboard?.writeText(q.answer)} className="hover:text-fg">copy</button>
                  <button type="button" onClick={() => onUseInChat(q.question)} className="hover:text-fg" title="Put this question in the chat box, to ask the agent itself">ask in chat</button>
                </div>
              </div>
            )}
          </div>
        ))}
        {pending && (
          <div className="space-y-1.5">
            <div className="ml-8 rounded-lg bg-panel-2 px-3 py-2 text-[13px] text-fg">{pending.question}</div>
            <div className="flex items-center gap-2 text-[12px] text-fg-dim">
              <svg className="h-3.5 w-3.5 animate-spin text-accent-ink" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" className="opacity-25" /><path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" /></svg>
              Thinking…
            </div>
          </div>
        )}
        {error && <div role="alert" className="text-[12px] text-danger-ink">{error}</div>}
      </div>
      <div className="flex items-end gap-2 border-t border-line px-2 py-2">
        <textarea
          ref={inputRef}
          rows={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void ask(); }
          }}
          maxLength={2000}
          aria-label="Side question"
          placeholder="Ask on the side…"
          className="block max-h-32 min-h-11 flex-1 resize-none bg-transparent px-2 py-2.5 text-[14px] text-fg outline-none placeholder:text-fg-dim md:min-h-9 md:py-2"
        />
        <button
          type="button"
          onClick={() => void ask()}
          disabled={!draft.trim() || !!pending}
          aria-label="Ask"
          className="grid h-11 w-11 flex-none place-items-center rounded-full bg-accent text-accent-foreground transition disabled:opacity-50 md:h-8 md:w-8"
        >
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
        </button>
      </div>
    </section>
  );
}
