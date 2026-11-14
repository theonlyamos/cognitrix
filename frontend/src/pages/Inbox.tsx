import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useNotifications, type InboxApproval, type InboxItem, type InboxNotification, type InboxOffer, type InboxQuestion, type InboxSkillDraft } from '@/context/NotificationsContext';
import { LoadingState } from '@/components/list-ui';
import { cn } from '@/lib/utils';
import { answerApproval } from '@/lib/approvals';

function itemTitle(item: InboxItem): string {
  if (item.kind === 'approval') return `Run ${item.tool_name}`;
  if (item.kind === 'notification' || item.kind === 'offer') return item.title;
  if (item.kind === 'skill_draft') return `Review the skill "${item.name}"`;
  return String(item.question.prompt || 'An agent asked a question');
}

function itemSource(item: InboxItem): string {
  if (item.kind === 'approval' && item.source === 'chat') return `${item.agent_name || 'Agent'} · chat`;
  if (item.kind === 'approval') {
    const step = item.step_index != null ? ` · step ${item.step_index + 1}` : '';
    return `${item.task_title || 'Task run'}${step}`;
  }
  if (item.kind === 'notification') return item.task_title || (item.session_id ? 'Chat' : 'Agent');
  if (item.kind === 'offer') return `${item.agent_name || 'Your assistant'} · check-in`;
  if (item.kind === 'skill_draft') return item.agent_name ? `Learned by ${item.agent_name}` : 'Learned from a chat';
  return 'Chat';
}

function Badge({ kind }: { kind: InboxItem['kind'] }) {
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0.5 font-mono text-[10px] tracking-[0.1em]',
        kind === 'approval' && 'bg-warn/15 text-warn-ink',
        kind === 'question' && 'bg-info/15 text-info-ink',
        kind === 'notification' && 'bg-accent/15 text-accent-ink',
        kind === 'offer' && 'bg-ok/15 text-ok',
        kind === 'skill_draft' && 'bg-violet/15 text-violet-ink',
      )}
    >
      {{ approval: 'APPROVAL', notification: 'ALERT', question: 'QUESTION', offer: 'OFFER', skill_draft: 'SKILL' }[kind]}
    </span>
  );
}

/** Pretty-print a tool call's parameters; a shell command shows as itself. */
function CallPreview({ approval }: { approval: InboxApproval }) {
  const params = approval.params ?? {};
  const command = typeof params.command === 'string' ? params.command : null;
  const text = command ?? JSON.stringify(params, null, 2);
  return (
    <div className="rounded-xl border border-line bg-panel">
      <div className="flex items-center gap-2 border-b border-line px-3.5 py-2.5">
        <span className="font-mono text-[12.5px] font-medium">{approval.tool_name}</span>
        {approval.risk_level && (
          <span className="rounded-full bg-danger/10 px-2 py-0.5 font-mono text-[10.5px] text-danger-ink">{approval.risk_level} risk</span>
        )}
        <span className="flex-1" />
        <span className="font-mono text-[11px] text-warn-ink">waiting</span>
      </div>
      <pre className="max-h-[260px] overflow-auto whitespace-pre-wrap break-words px-3.5 py-3 font-mono text-[12px] leading-relaxed text-fg">{text}</pre>
    </div>
  );
}

function ApprovalDetail({ item, onDone }: { item: InboxApproval; onDone: () => void }) {
  const [busy, setBusy] = useState<'approve' | 'deny' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fromChat = item.source === 'chat';
  const decide = async (approved: boolean) => {
    if (!fromChat && !item.task_id) return;
    setBusy(approved ? 'approve' : 'deny');
    setError(null);
    try {
      await answerApproval(item, approved);
      onDone();
    } catch (err) {
      setError(errorMessage(err, 'Could not send your answer.'));
    } finally {
      setBusy(null);
    }
  };

  // A / D answer the open approval, like the hint under the buttons says.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (busy || e.ctrlKey || e.metaKey || e.altKey || target?.closest('input, textarea, [contenteditable="true"]')) return;
      if (e.key === 'a' || e.key === 'A') void decide(true);
      if (e.key === 'd' || e.key === 'D') void decide(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  });

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="font-mono text-[10.5px] tracking-[0.14em] text-warn-ink">APPROVAL NEEDED</div>
        <div className="mt-1 font-mono text-[12px] text-fg-dim">{itemSource(item)}</div>
        <h2 className="mt-2 text-[20px] font-semibold leading-snug">{fromChat ? `${item.agent_name || 'The agent'} wants to run ${item.tool_name}` : `The agent wants to run ${item.tool_name}`}</h2>
        {item.details && <p className="mt-2 text-[13.5px] leading-relaxed text-fg-dim">{item.details}</p>}
      </div>
      <CallPreview approval={item} />
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide(true)}
          className="h-10 rounded-[9px] bg-accent px-5 text-[13.5px] font-semibold text-accent-foreground disabled:opacity-60"
        >
          {busy === 'approve' ? 'Approving…' : 'Approve'}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide(false)}
          className="h-10 rounded-[9px] border border-danger/40 px-5 text-[13.5px] text-danger-ink disabled:opacity-60"
        >
          {busy === 'deny' ? 'Denying…' : 'Deny'}
        </button>
        <span className="font-mono text-[11px] text-fg-dim">A / D</span>
        <span className="flex-1" />
        {fromChat && item.href ? (
          <Link to={item.href} className="text-[13px] text-accent-ink hover:underline">
            Open chat
          </Link>
        ) : item.task_id && (
          <Link to={`/tasks/${item.task_id}`} className="text-[13px] text-accent-ink hover:underline">
            Open task
          </Link>
        )}
      </div>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        {fromChat
          ? 'The same request shows in the chat, so you can answer in either place. The agent waits until you do.'
          : 'The same approval shows on the task page, so you can answer in either place. Until then the run stays paused and never continues on its own. Unanswered approvals expire after 30 minutes and count as denied.'}
      </p>
    </div>
  );
}

function QuestionDetail({ item, onDone }: { item: InboxQuestion; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const q = item.question;

  const answer = async (body: { option_id?: string; text?: string }) => {
    setBusy(true);
    setError(null);
    try {
      await api.post('/agents/question', { request_id: item.id, action: 'answer', ...body });
      onDone();
    } catch (err) {
      setError(errorMessage(err, 'Could not send your answer.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="font-mono text-[10.5px] tracking-[0.14em] text-info-ink">AGENT IS ASKING</div>
        <h2 className="mt-2 text-[20px] font-semibold leading-snug">{String(q.prompt || 'A question from your chat')}</h2>
        {typeof q.details === 'string' && q.details && <p className="mt-2 text-[13.5px] leading-relaxed text-fg-dim">{q.details}</p>}
      </div>
      {q.options && q.options.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {q.options.map((option) => (
            <button
              key={option.id}
              type="button"
              disabled={busy}
              onClick={() => answer({ option_id: option.id })}
              className={cn(
                'min-h-10 rounded-[10px] border px-4 py-2 text-left text-[13.5px] disabled:opacity-60',
                option.id === q.recommended_option_id ? 'border-accent bg-accent/10' : 'border-line hover:bg-panel-2',
              )}
            >
              <span className="block font-medium">{option.label}</span>
              {option.description && <span className="block text-[12px] text-fg-dim">{option.description}</span>}
            </button>
          ))}
        </div>
      )}
      {q.allow_free_text !== false && (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) void answer({ text: text.trim() });
          }}
        >
          <label htmlFor="inbox-answer" className="sr-only">Your answer</label>
          <input
            id="inbox-answer"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type an answer…"
            className="h-10 flex-1 rounded-[9px] border border-line bg-panel px-3 text-[13.5px] outline-none focus:border-accent"
          />
          <button type="submit" disabled={busy || !text.trim()} className="h-10 rounded-[9px] bg-accent px-4 text-[13.5px] font-semibold text-accent-foreground disabled:opacity-60">
            Send
          </button>
        </form>
      )}
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
      <p className="text-[12.5px] text-fg-dim">
        Chat questions answer themselves with the recommended option if nobody replies in time.{' '}
        <Link to="/home" className="text-accent-ink hover:underline">Open the chat</Link>
      </p>
    </div>
  );
}

function NotificationDetail({ item, onDone }: { item: InboxNotification; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dismiss = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/notifications/${item.id}/read`);
      onDone();
    } catch (err) {
      setError(errorMessage(err, 'Could not dismiss this alert.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="font-mono text-[10.5px] tracking-[0.14em] text-accent-ink">AGENT ALERT</div>
        <div className="mt-1 font-mono text-[12px] text-fg-dim">{itemSource(item)}</div>
        <h2 className="mt-2 text-[20px] font-semibold leading-snug">{item.title}</h2>
        {item.message && <p className="mt-2 whitespace-pre-wrap text-[13.5px] leading-relaxed text-fg">{item.message}</p>}
      </div>
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={dismiss}
          className="h-10 rounded-[9px] bg-accent px-5 text-[13.5px] font-semibold text-accent-foreground disabled:opacity-60"
        >
          {busy ? 'Dismissing…' : 'Dismiss'}
        </button>
        <span className="flex-1" />
        {item.link && item.link.startsWith('/') && (
          <Link to={item.link} className="text-[13px] font-medium text-accent-ink hover:underline">
            {item.title.startsWith('Reconnect ') ? item.title : 'Open'}
          </Link>
        )}
        {item.task_id && (
          <Link to={`/tasks/${item.task_id}`} className="text-[13px] text-accent-ink hover:underline">
            Open task
          </Link>
        )}
        {!item.task_id && item.session_id && (
          <Link to={`/home?chat=${encodeURIComponent(item.session_id)}`} className="text-[13px] text-accent-ink hover:underline">
            Open chat
          </Link>
        )}
      </div>
    </div>
  );
}

function OfferDetail({ item, onDone }: { item: InboxOffer; onDone: () => void }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<'accept' | 'dismiss' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const answer = async (action: 'accept' | 'dismiss') => {
    setBusy(action);
    setError(null);
    try {
      const { data } = await api.post<{ session_id?: string }>(`/offers/${item.id}/${action}`);
      onDone();
      if (action === 'accept' && data.session_id) navigate(`/home?chat=${encodeURIComponent(data.session_id)}`);
    } catch (err) {
      setError(errorMessage(err, 'Could not send your answer.'));
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="font-mono text-[10.5px] tracking-[0.14em] text-ok">OFFER TO HELP</div>
        <div className="mt-1 font-mono text-[12px] text-fg-dim">{itemSource(item)}</div>
        <h2 className="mt-2 text-[20px] font-semibold leading-snug">{item.title}</h2>
        {item.details && <p className="mt-2 whitespace-pre-wrap text-[13.5px] leading-relaxed text-fg">{item.details}</p>}
      </div>
      {item.plan && (
        <div className="rounded-xl border border-line bg-panel">
          <div className="border-b border-line px-3.5 py-2.5 text-[12.5px] font-medium">What it will do if you accept</div>
          <p className="whitespace-pre-wrap px-3.5 py-3 text-[13px] leading-relaxed text-fg">{item.plan}</p>
        </div>
      )}
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy !== null} onClick={() => void answer('accept')}
          className="h-10 rounded-[9px] bg-accent px-5 text-[13.5px] font-semibold text-accent-foreground disabled:opacity-60">
          {busy === 'accept' ? 'Starting…' : 'Accept'}
        </button>
        <button type="button" disabled={busy !== null} onClick={() => void answer('dismiss')}
          className="h-10 rounded-[9px] border border-line px-5 text-[13.5px] disabled:opacity-60">
          {busy === 'dismiss' ? 'Dismissing…' : 'Dismiss'}
        </button>
        <span className="flex-1" />
        <Link to={`/agents/${item.agent_id}/checkins`} className="text-[13px] text-accent-ink hover:underline">Check-in settings</Link>
      </div>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        Accepting starts a chat where {item.agent_name || 'the agent'} does this. Risky steps still ask you first. A dismissed offer isn't made again.
      </p>
    </div>
  );
}

function SkillDraftDetail({ item, onDone }: { item: InboxSkillDraft; onDone: () => void }) {
  const [name, setName] = useState(item.name);
  const [description, setDescription] = useState(item.description);
  const [body, setBody] = useState(item.body);
  const [busy, setBusy] = useState<'approve' | 'share' | 'reject' | 'save' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const edited = name !== item.name || description !== item.description || body !== item.body;

  const saveEdits = async () => {
    await api.patch(`/skill-drafts/${item.id}`, { name, description, body });
  };

  const run = async (action: 'approve' | 'share' | 'reject' | 'save') => {
    setBusy(action);
    setError(null);
    setSaved(false);
    try {
      if (action !== 'reject' && edited) await saveEdits();
      if (action === 'save') {
        setSaved(true);
        setBusy(null);
        return;
      }
      if (action === 'reject') await api.post(`/skill-drafts/${item.id}/reject`);
      else await api.post(`/skill-drafts/${item.id}/approve`, { everyone: action === 'share' });
      onDone();
    } catch (err) {
      setError(errorMessage(err, 'Could not save that.'));
      setBusy(null);
    }
  };

  const field = 'w-full rounded-[9px] border border-line bg-panel px-3 text-[13.5px] outline-none focus:border-accent';
  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="font-mono text-[10.5px] tracking-[0.14em] text-violet-ink">SKILL TO REVIEW</div>
        <div className="mt-1 font-mono text-[12px] text-fg-dim">{itemSource(item)}</div>
        <h2 className="mt-2 text-[20px] font-semibold leading-snug">Save “{item.name}” as a skill?</h2>
        <p className="mt-2 text-[13.5px] leading-relaxed text-fg-dim">
          A skill is a set of instructions your agents can load when a job like this comes up again. Read it, fix anything that's off, then save it. Until then nothing uses it.
        </p>
      </div>
      <label className="flex flex-col gap-1.5 text-[13px] font-medium">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={64} className={cn(field, 'h-10 font-mono')} />
      </label>
      <label className="flex flex-col gap-1.5 text-[13px] font-medium">
        When to use it
        <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1024} className={cn(field, 'h-10')} />
      </label>
      <label className="flex flex-col gap-1.5 text-[13px] font-medium">
        Instructions
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={16}
          className={cn(field, 'resize-y py-2.5 font-mono text-[12.5px] leading-relaxed')} />
      </label>
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy !== null} onClick={() => void run('approve')}
          className="h-10 rounded-[9px] bg-accent px-5 text-[13.5px] font-semibold text-accent-foreground disabled:opacity-60">
          {busy === 'approve' ? 'Saving…' : 'Save to my skills'}
        </button>
        {item.can_share && (
          <button type="button" disabled={busy !== null} onClick={() => void run('share')}
            className="h-10 rounded-[9px] border border-line px-5 text-[13.5px] disabled:opacity-60">
            {busy === 'share' ? 'Saving…' : 'Save for everyone'}
          </button>
        )}
        {edited && (
          <button type="button" disabled={busy !== null} onClick={() => void run('save')}
            className="h-10 rounded-[9px] border border-line px-5 text-[13.5px] disabled:opacity-60">
            {busy === 'save' ? 'Saving…' : 'Save edits'}
          </button>
        )}
        <button type="button" disabled={busy !== null} onClick={() => void run('reject')}
          className="h-10 rounded-[9px] border border-danger/40 px-5 text-[13.5px] text-danger-ink disabled:opacity-60">
          {busy === 'reject' ? 'Rejecting…' : 'Reject'}
        </button>
        {saved && <span role="status" className="text-[13px] text-fg-dim">Edits saved.</span>}
        <span className="flex-1" />
        {item.session_id && item.mine && (
          <Link to={`/home?chat=${encodeURIComponent(item.session_id)}`} className="text-[13px] text-accent-ink hover:underline">Open the chat it came from</Link>
        )}
      </div>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        Your own skills are only used in your chats and tasks. {item.can_share
          ? 'Saving for everyone lets every agent on this server load it.'
          : 'An admin can share a skill with everyone on this server.'} Manage them in <Link to="/settings/skills" className="text-accent-ink hover:underline">Settings › Skills</Link>.
      </p>
    </div>
  );
}

/** Everything waiting on you: approvals from paused task runs, questions from chats, and agent alerts. */
export default function Inbox() {
  const { inbox, inboxLoaded, refreshInbox } = useNotifications();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selected = useMemo(() => inbox.find((i) => i.id === selectedId) ?? inbox[0] ?? null, [inbox, selectedId]);
  const approvals = inbox.filter((i) => i.kind === 'approval').length;
  const waitingWord = approvals === 1 ? 'approval' : 'approvals';

  const done = () => {
    setSelectedId(null);
    void refreshInbox();
  };

  return (
    <div className="flex h-full min-w-0 flex-1 overflow-hidden bg-bg text-fg">
      <aside
        aria-label="Waiting on you"
        className={cn('flex w-[320px] flex-none flex-col border-r border-line bg-panel max-md:w-full max-md:border-r-0', selectedId && 'max-md:hidden')}
      >
        <div className="border-b border-line px-4 py-4">
          <h1 className="text-[17px] font-semibold">Inbox</h1>
          <p className="mt-1 text-[12.5px] text-fg-dim">
            {inbox.length === 0
              ? 'Nothing is waiting on you.'
              : `${inbox.length} waiting${approvals ? ` · ${approvals} ${waitingWord}` : ''}`}
          </p>
        </div>
        <ul className="flex-1 overflow-y-auto p-2">
          {!inboxLoaded && <LoadingState label="loading inbox…" />}
          {inbox.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => setSelectedId(item.id)}
                aria-current={selected?.id === item.id ? 'true' : undefined}
                className={cn(
                  'mb-1 w-full rounded-[10px] border px-3 py-3 text-left transition-colors',
                  selected?.id === item.id ? 'border-line bg-panel-2' : 'border-transparent hover:bg-panel-2',
                )}
              >
                <div className="flex items-center gap-2">
                  <Badge kind={item.kind} />
                  <span className="truncate text-[12px] text-fg-dim">{itemSource(item)}</span>
                </div>
                <div className="mt-1.5 line-clamp-2 text-[13.5px] font-medium">{itemTitle(item)}</div>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className={cn('min-w-0 flex-1 overflow-y-auto px-7 py-6 max-md:px-4', selectedId ? 'block' : 'hidden md:block')}>
        {selected ? (
          <div className="mx-auto max-w-[760px]">
            <button type="button" onClick={() => setSelectedId(null)} className="mb-4 text-[13px] text-fg-dim hover:text-fg md:hidden">
              ← All items
            </button>
            {selected.kind === 'approval' ? (
              <ApprovalDetail key={selected.id} item={selected} onDone={done} />
            ) : selected.kind === 'notification' ? (
              <NotificationDetail key={selected.id} item={selected} onDone={done} />
            ) : selected.kind === 'offer' ? (
              <OfferDetail key={selected.id} item={selected} onDone={done} />
            ) : selected.kind === 'skill_draft' ? (
              <SkillDraftDetail key={selected.id} item={selected} onDone={done} />
            ) : (
              <QuestionDetail key={selected.id} item={selected} onDone={done} />
            )}
          </div>
        ) : (
          inboxLoaded && (
            <div className="grid h-full place-items-center text-center">
              <div>
                <div className="text-[15px] font-medium">You're all caught up</div>
                <p className="mt-1 text-[13px] text-fg-dim">When a task pauses on a risky step, an agent asks something, offers to help or drafts a skill, or an automation sends an alert, it shows up here.</p>
              </div>
            </div>
          )
        )}
      </section>
    </div>
  );
}
