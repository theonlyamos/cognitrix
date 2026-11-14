import { useState, type FormEvent } from 'react';
import { Bot, MapPin, X } from 'lucide-react';
import { ago, type CommentAnchor, type CommentEntry, type CommentThread, type PageDetail } from '@/lib/artifactPages';
import { cn } from '@/lib/utils';

function Entry({ entry }: { entry: CommentEntry }) {
  return (
    <div className="flex gap-2.5">
      <span className={cn('grid h-6 w-6 flex-none place-items-center border border-line font-mono text-[10.5px]',
        entry.is_agent ? 'rounded-md bg-panel' : 'rounded-full bg-panel-2')}>
        {entry.author_name.slice(0, 1).toUpperCase() || '?'}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium">
          {entry.author_name}
          <span className="ml-1.5 font-mono text-[10.5px] font-normal text-fg-dim">{entry.is_agent ? 'agent · ' : ''}{ago(entry.created)}</span>
        </div>
        <p className="mt-0.5 whitespace-pre-wrap break-words text-[13px] leading-snug">{entry.body}</p>
        {entry.sent_to_agent && (
          <span className="mt-1.5 inline-flex items-center gap-1 rounded-md bg-accent/15 px-1.5 py-0.5 font-mono text-[10.5px] text-accent-ink">
            <Bot size={11} /> Sent to the agent
          </span>
        )}
      </div>
    </div>
  );
}

interface CommentsRailProps {
  page: PageDetail;
  threads: CommentThread[];
  canSend: boolean;
  activeThread: string | null;
  onActive: (id: string | null) => void;
  /** The spot picked on the page for a new comment, if any. */
  draftAnchor: CommentAnchor | null;
  picking: boolean;
  onPick: (on: boolean) => void;
  onClearAnchor: () => void;
  onSubmit: (body: string, opts: { threadId?: string; sendToAgent: boolean }) => Promise<string>;
  onResolve: (threadId: string, resolved: boolean) => void;
  onShowVersion: (version: number) => void;
  onClose: () => void;
}

/** Comment threads beside an artifact, and the box to add one. */
export function CommentsRail({
  page, threads, canSend, activeThread, onActive, draftAnchor, picking, onPick, onClearAnchor, onSubmit, onResolve, onShowVersion, onClose,
}: CommentsRailProps) {
  const [text, setText] = useState('');
  const [send, setSend] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [showResolved, setShowResolved] = useState(false);
  const agentName = page.agent?.name || 'the agent';
  const open = threads.filter((t) => !t.resolved);
  const shown = showResolved ? threads.filter((t) => t.resolved) : open;
  const replyTo = activeThread ? threads.find((t) => t.id === activeThread && !t.resolved) : undefined;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setNote('');
    try {
      const status = await onSubmit(text.trim(), { threadId: replyTo?.id, sendToAgent: send && canSend });
      setText('');
      setNote(status === 'sent' ? `Sent to ${agentName}. Its reply will show here.` : status);
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not post the comment.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="Comments" className="flex h-full min-h-0 flex-col bg-bg">
      <header className="flex h-11 flex-none items-center gap-2 border-b border-line px-3">
        <h3 className="text-[13.5px] font-semibold">Comments</h3>
        <span className="rounded-md bg-panel-2 px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim">{open.length} open</span>
        <button type="button" onClick={() => setShowResolved((v) => !v)} className="ml-auto font-mono text-[11px] text-fg-dim hover:text-fg">
          {showResolved ? 'show open' : 'show resolved'}
        </button>
        <button type="button" onClick={onClose} aria-label="Close comments" className="grid h-8 w-8 place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg"><X size={15} /></button>
      </header>
      <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3">
        {shown.length === 0 && (
          <p className="px-1 pt-2 text-[12.5px] leading-relaxed text-fg-dim">
            {showResolved ? 'No resolved comments.' : threads.length > 0 ? `Nothing open. ${threads.length} resolved ${threads.length === 1 ? 'thread is' : 'threads are'} under “show resolved”.` : `No comments yet. Use “Pin to a spot”, click the page, and write what you think. ${canSend ? `Tick “Send to ${agentName}” to have it make the change.` : ''}`}
          </p>
        )}
        {shown.map((thread) => {
          const number = threads.indexOf(thread) + 1;
          const newest = [thread, ...thread.replies].reduce((max, e) => Math.max(max, e.version), 0);
          return (
            <article key={thread.id} onClick={() => onActive(thread.id)}
              className={cn('cursor-pointer rounded-[10px] border bg-panel p-3', activeThread === thread.id ? 'border-accent-ink/60' : 'border-line')}>
              <div className="mb-2 flex items-center gap-1.5 text-[12px] text-fg-dim">
                <span className="grid h-4.5 min-w-[18px] place-items-center rounded-full bg-fg px-1 font-mono text-[10px] text-bg">{number}</span>
                {thread.anchor.text ? <span className="min-w-0 truncate border-l-2 border-accent pl-1.5">{thread.anchor.text}</span> : <span>Whole page</span>}
                <span className="ml-auto flex-none font-mono text-[10.5px]">v{thread.version}</span>
              </div>
              <div className="space-y-2.5">
                <Entry entry={thread} />
                {thread.replies.map((reply) => <Entry key={reply.id} entry={reply} />)}
              </div>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {newest > 0 && newest !== thread.version && page.role !== 'view' && (
                  <button type="button" onClick={(e) => { e.stopPropagation(); onShowVersion(newest); }}
                    className="inline-flex h-7 items-center rounded-md border border-line px-2 text-[12px] hover:bg-panel-2">View v{newest}</button>
                )}
                <button type="button" onClick={(e) => { e.stopPropagation(); onResolve(thread.id, !thread.resolved); }}
                  className="inline-flex h-7 items-center rounded-md px-2 text-[12px] text-fg-dim hover:bg-panel-2 hover:text-fg">
                  {thread.resolved ? 'Reopen' : 'Resolve'}
                </button>
              </div>
            </article>
          );
        })}
      </div>
      <form onSubmit={submit} className="flex-none border-t border-line p-3">
        {(replyTo || draftAnchor) && (
          <div className="mb-2 flex items-center gap-1.5 text-[12px] text-fg-dim">
            {replyTo ? <span className="min-w-0 truncate">Replying to {replyTo.author_name}</span>
              : <span className="min-w-0 truncate"><MapPin size={12} className="mr-1 inline" />{draftAnchor?.text || 'A spot on the page'}</span>}
            <button type="button" className="ml-auto flex-none hover:text-fg" onClick={() => (replyTo ? onActive(null) : onClearAnchor())}>clear</button>
          </div>
        )}
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={4000}
          aria-label="Comment" placeholder={replyTo ? 'Reply…' : canSend ? `Comment, or @agent to ask ${agentName} for a change…` : 'Comment…'}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void submit(e); }}
          className="w-full resize-none rounded-[10px] border border-line bg-panel px-3 py-2 text-[13px] placeholder:text-fg-dim focus:border-accent" />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {!replyTo && (
            <button type="button" onClick={() => onPick(!picking)} aria-pressed={picking}
              className={cn('inline-flex h-8 items-center gap-1 rounded-md border px-2 text-[12px]', picking ? 'border-accent-ink bg-accent/15 text-accent-ink' : 'border-line text-fg-dim hover:text-fg')}>
              <MapPin size={12} /> {picking ? 'Click the page…' : 'Pin to a spot'}
            </button>
          )}
          {canSend && (
            <label className="inline-flex cursor-pointer items-center gap-1.5 text-[12.5px]">
              <input type="checkbox" className="h-3.5 w-3.5 accent-accent-ink" checked={send} onChange={(e) => setSend(e.target.checked)} />
              Send to <span className="font-medium">{agentName}</span>
            </label>
          )}
          <button type="submit" disabled={busy || !text.trim()}
            className="ml-auto inline-flex h-8 items-center rounded-md bg-accent px-3 text-[12.5px] font-semibold text-accent-foreground disabled:opacity-50">
            {busy ? 'Posting…' : 'Comment'}
          </button>
        </div>
        {note && <p aria-live="polite" className="mt-2 text-[12px] text-fg-dim">{note}</p>}
      </form>
    </section>
  );
}
