import { PanelButton, PanelCard } from '@/components/cards/PanelCard';
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useNotifications, type InboxApproval, type InboxItem, type InboxNotification, type InboxOffer, type InboxQuestion, type InboxSkillDraft } from '@/context/NotificationsContext';
import { LoadingState, PageHeader, ListToolbar, FilterChips } from '@/components/list-ui';
import { RecentNotifications } from '@/pages/Notifications';
import { cn } from '@/lib/utils';
import { answerApproval } from '@/lib/approvals';
import { tr } from '@/lib/i18n';

function itemTitle(item: InboxItem): string {
  if (item.kind === 'approval') return tr('ui.inbox.runToolName', { tool_name: item.tool_name });
  if (item.kind === 'notification' || item.kind === 'offer') return item.title;
  if (item.kind === 'skill_draft') return tr('ui.inbox.reviewTheSkillName', { name: item.name });
  return String(item.question.prompt || 'An agent asked a question');
}

function itemSource(item: InboxItem): string {
  if (item.kind === 'approval' && item.source === 'chat') return tr('ui.inbox.agentNameChat', { agent_name: item.agent_name || 'Agent' });
  if (item.kind === 'approval') {
    const step = item.step_index != null ? ` · step ${item.step_index + 1}` : '';
    return `${item.task_title || 'Task run'}${step}`;
  }
  if (item.kind === 'notification') return item.task_title || (item.session_id ? 'Chat' : 'Agent');
  if (item.kind === 'offer') return tr('ui.inbox.agentNameCheckIn', { agent_name: item.agent_name || 'Your assistant' });
  if (item.kind === 'skill_draft') return item.agent_name ? tr('ui.inbox.learnedByAgentName', { agent_name: item.agent_name }) : tr('ui.inbox.learnedFromAChat');
  return 'Chat';
}

function Badge({ kind }: { kind: InboxItem['kind'] }) {
  return (
    <span
      className={cn(
        'rounded-full px-2 py-0.5 text-[11px] font-medium',
        kind === 'approval' && 'bg-warn/15 text-warn-ink',
        kind === 'question' && 'bg-info/15 text-info-ink',
        kind === 'notification' && 'bg-accent/15 text-accent-ink',
        kind === 'offer' && 'bg-ok/15 text-ok',
        kind === 'skill_draft' && 'bg-violet/15 text-violet-ink',
      )}
    >
      {{ approval: { label: tr('ui.inbox.approval') }, notification: { label: tr('ui.inbox.alert') }, question: { label: tr('ui.inbox.question') }, offer: { label: tr('ui.inbox.offer') }, skill_draft: { label: tr('ui.inbox.skill') } }[kind].label}
    </span>
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
      setError(errorMessage(err, tr('ui.inbox.couldNotSendYourAnswer')));
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

  const params = item.params ?? {};
  const command = typeof params.command === 'string' ? params.command : null;
  return (
    <div className="flex flex-col gap-4">
      <PanelCard
        tone="danger"
        tag="⚠ approval"
        title={fromChat ? tr('ui.inbox.agentNameWantsToRun', { agent_name: item.agent_name || 'The agent', tool_name: item.tool_name }) : tr('ui.inbox.theAgentWantsToRun', { tool_name: item.tool_name })}
        subtitle={item.details || itemSource(item)}
        fields={[
          { label: tr('ui.inbox.tool'), value: <span className="font-mono text-[13px]">{item.tool_name}</span> },
          ...(item.risk_level ? [{ label: tr('ui.inbox.risk'), value: <span className="text-danger-ink">{item.risk_level}</span> }] : []),
          { label: tr('ui.inbox.from'), value: itemSource(item) },
        ]}
        footer={(
          <>
            <PanelButton kind="primary" disabled={busy !== null} onClick={() => void decide(true)}>
              {busy === 'approve' ? tr('ui.inbox.approving') : tr('ui.inbox.approve')}
            </PanelButton>
            <PanelButton kind="danger" disabled={busy !== null} onClick={() => void decide(false)}>
              {busy === 'deny' ? tr('ui.inbox.denying') : tr('ui.inbox.deny')}
            </PanelButton>
            <span className="font-mono text-[11px] text-fg-dim">{tr('ui.inbox.aD')}</span>
            <span className="flex-1" />
            {fromChat && item.href ? (
              <Link to={item.href} className="text-[13px] text-accent-ink hover:underline">{tr('ui.inbox.openChat')}</Link>
            ) : item.task_id && (
              <Link to={`/tasks/${item.task_id}`} className="text-[13px] text-accent-ink hover:underline">{tr('ui.inbox.openTask')}</Link>
            )}
          </>
        )}
      >
        <pre className="max-h-[260px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-panel-2 px-3 py-2.5 font-mono text-[12px] leading-relaxed text-fg">
          {command ?? JSON.stringify(params, null, 2)}
        </pre>
      </PanelCard>
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        {fromChat
          ? tr('ui.inbox.theSameRequestShowsIn')
          : tr('ui.inbox.theSameApprovalShowsOn')}
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
      setError(errorMessage(err, tr('ui.inbox.couldNotSendYourAnswer')));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <PanelCard
        tag="? question"
        title={String(q.prompt || 'A question from your chat')}
        subtitle={typeof q.details === 'string' && q.details ? q.details : itemSource(item)}
        footer={(
          <>
            <span className="text-[12.5px] text-fg-dim">{tr('ui.inbox.chatQuestionsAnswerThemselvesWith')}</span>
            <span className="flex-1" />
            <Link to="/home" className="text-[13px] text-accent-ink hover:underline">{tr('ui.inbox.openTheChat')}</Link>
          </>
        )}
      >
        {q.options && q.options.length > 0 && (
          <div className="grid gap-2 sm:grid-cols-2">
            {q.options.map((option) => (
              <button
                key={option.id}
                type="button"
                disabled={busy}
                onClick={() => answer({ option_id: option.id })}
                className={cn(
                  'min-h-10 rounded-md border px-3 py-2 text-left text-[13.5px] disabled:opacity-60',
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
            className={cn('flex gap-2', q.options && q.options.length > 0 && 'mt-3')}
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) void answer({ text: text.trim() });
            }}
          >
            <label htmlFor="inbox-answer" className="sr-only">{tr('ui.inbox.yourAnswer')}</label>
            <input
              id="inbox-answer"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={tr('ui.inbox.typeAnAnswer')}
              className="h-10 flex-1 rounded-md border border-line bg-panel px-3 text-[13.5px] outline-none focus:border-accent"
            />
            <PanelButton type="submit" kind="primary" disabled={busy || !text.trim()}>{tr('ui.inbox.send')}</PanelButton>
          </form>
        )}
      </PanelCard>
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
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
      setError(errorMessage(err, tr('ui.inbox.couldNotDismissThisAlert')));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <PanelCard
        tag="alert"
        title={item.title}
        subtitle={itemSource(item)}
        footer={(
          <>
            <PanelButton kind="primary" disabled={busy} onClick={() => void dismiss()}>
              {busy ? tr('ui.inbox.dismissing') : tr('ui.inbox.dismiss')}
            </PanelButton>
            <span className="flex-1" />
            {item.link && item.link.startsWith('/') && (
              <Link to={item.link} className="text-[13px] font-medium text-accent-ink hover:underline">
                {item.title.startsWith('Reconnect ') ? item.title : tr('ui.inbox.open')}
              </Link>
            )}
            {item.task_id && (
              <Link to={`/tasks/${item.task_id}`} className="text-[13px] text-accent-ink hover:underline">
                {tr('ui.inbox.openTask')}
              </Link>
            )}
            {!item.task_id && item.session_id && (
              <Link to={`/home?chat=${encodeURIComponent(item.session_id)}`} className="text-[13px] text-accent-ink hover:underline">
                {tr('ui.inbox.openChat')}
              </Link>
            )}
          </>
        )}
      >
        {item.message ? <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-fg">{item.message}</p> : undefined}
      </PanelCard>
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
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
      setError(errorMessage(err, tr('ui.inbox.couldNotSendYourAnswer')));
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="font-mono text-[10.5px] tracking-[0.14em] text-ok">{tr('ui.inbox.offerToHelp')}</div>
        <div className="mt-1 font-mono text-[12px] text-fg-dim">{itemSource(item)}</div>
        <h2 className="mt-2 text-[20px] font-semibold leading-snug">{item.title}</h2>
        {item.details && <p className="mt-2 whitespace-pre-wrap text-[13.5px] leading-relaxed text-fg">{item.details}</p>}
      </div>
      {item.plan && (
        <div className="rounded-xl border border-line bg-panel">
          <div className="border-b border-line px-3.5 py-2.5 text-[12.5px] font-medium">{tr('ui.inbox.whatItWillDoIf')}</div>
          <p className="whitespace-pre-wrap px-3.5 py-3 text-[13px] leading-relaxed text-fg">{item.plan}</p>
        </div>
      )}
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy !== null} onClick={() => void answer('accept')}
          className="h-10 rounded-[9px] bg-accent px-5 text-[13.5px] font-semibold text-accent-foreground disabled:opacity-60">
          {busy === 'accept' ? tr('ui.inbox.starting') : tr('ui.inbox.accept')}
        </button>
        <button type="button" disabled={busy !== null} onClick={() => void answer('dismiss')}
          className="h-10 rounded-[9px] border border-line px-5 text-[13.5px] disabled:opacity-60">
          {busy === 'dismiss' ? tr('ui.inbox.dismissing') : tr('ui.inbox.dismiss')}
        </button>
        <span className="flex-1" />
        <Link to={`/agents/${item.agent_id}/checkins`} className="text-[13px] text-accent-ink hover:underline">{tr('ui.inbox.checkInSettings')}</Link>
      </div>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        {tr('ui.inbox.acceptingStartsAChatWhere', { agent_name: item.agent_name || tr('ui.inbox.theAgent') })}
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
      setError(errorMessage(err, tr('ui.inbox.couldNotSaveThat')));
      setBusy(null);
    }
  };

  const field = 'w-full rounded-[9px] border border-line bg-panel px-3 text-[13.5px] outline-none focus:border-accent';
  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="font-mono text-[10.5px] tracking-[0.14em] text-violet-ink">{tr('ui.inbox.skillToReview')}</div>
        <div className="mt-1 font-mono text-[12px] text-fg-dim">{itemSource(item)}</div>
        <h2 className="mt-2 text-[20px] font-semibold leading-snug">{tr('ui.inbox.saveNameAsASkill', { name: item.name })}</h2>
        <p className="mt-2 text-[13.5px] leading-relaxed text-fg-dim">
          {tr('ui.inbox.aSkillIsASet')}
        </p>
      </div>
      <label className="flex flex-col gap-1.5 text-[13px] font-medium">
        {tr('ui.inbox.name')}
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={64} className={cn(field, 'h-10 font-mono')} />
      </label>
      <label className="flex flex-col gap-1.5 text-[13px] font-medium">
        {tr('ui.inbox.whenToUseIt')}
        <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1024} className={cn(field, 'h-10')} />
      </label>
      <label className="flex flex-col gap-1.5 text-[13px] font-medium">
        {tr('ui.inbox.instructions')}
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={16}
          className={cn(field, 'resize-y py-2.5 font-mono text-[12.5px] leading-relaxed')} />
      </label>
      {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy !== null} onClick={() => void run('approve')}
          className="h-10 rounded-[9px] bg-accent px-5 text-[13.5px] font-semibold text-accent-foreground disabled:opacity-60">
          {busy === 'approve' ? tr('ui.inbox.saving') : tr('ui.inbox.saveToMySkills')}
        </button>
        {item.can_share && (
          <button type="button" disabled={busy !== null} onClick={() => void run('share')}
            className="h-10 rounded-[9px] border border-line px-5 text-[13.5px] disabled:opacity-60">
            {busy === 'share' ? tr('ui.inbox.saving') : tr('ui.inbox.saveForEveryone')}
          </button>
        )}
        {edited && (
          <button type="button" disabled={busy !== null} onClick={() => void run('save')}
            className="h-10 rounded-[9px] border border-line px-5 text-[13.5px] disabled:opacity-60">
            {busy === 'save' ? tr('ui.inbox.saving') : tr('ui.inbox.saveEdits')}
          </button>
        )}
        <button type="button" disabled={busy !== null} onClick={() => void run('reject')}
          className="h-10 rounded-[9px] border border-danger/40 px-5 text-[13.5px] text-danger-ink disabled:opacity-60">
          {busy === 'reject' ? tr('ui.inbox.rejecting') : tr('ui.inbox.reject')}
        </button>
        {saved && <span role="status" className="text-[13px] text-fg-dim">{tr('ui.inbox.editsSaved')}</span>}
        <span className="flex-1" />
        {item.session_id && item.mine && (
          <Link to={`/home?chat=${encodeURIComponent(item.session_id)}`} className="text-[13px] text-accent-ink hover:underline">{tr('ui.inbox.openTheChatItCame')}</Link>
        )}
      </div>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        {tr('ui.inbox.yourOwnSkillsAreOnly', { value: item.can_share
          ? tr('ui.inbox.savingForEveryoneLetsEvery')
          : tr('ui.inbox.anAdminCanShareA') })}{' '}<Link to="/settings/skills" className="text-accent-ink hover:underline">{tr('ui.inbox.settingsSkills')}</Link>.
      </p>
    </div>
  );
}

type InboxTab = 'needs' | 'recent';

/** One place for what agents send you: what is waiting on an answer, and what happened lately. */
export default function Inbox() {
  const { inbox, inboxLoaded, refreshInbox, unread } = useNotifications();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const tab: InboxTab = params.get('tab') === 'recent' ? 'recent' : 'needs';
  const setTab = (next: InboxTab) => setParams(next === 'recent' ? { tab: 'recent' } : {}, { replace: true });

  const selected = useMemo(() => inbox.find((i) => i.id === selectedId) ?? inbox[0] ?? null, [inbox, selectedId]);

  const done = () => {
    setSelectedId(null);
    void refreshInbox();
  };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.inbox.inbox')} subtitle={tr('ui.inbox.questionsAndApprovalsWaitingOn')} />
      <ListToolbar>
        <FilterChips<InboxTab>
          label={tr('ui.inbox.show')}
          value={tab}
          onChange={setTab}
          options={[
            { id: 'needs', label: tr('ui.inbox.needsYou'), count: inbox.length },
            { id: 'recent', label: tr('ui.inbox.recent'), count: unread },
          ]}
        />
      </ListToolbar>
      {tab === 'recent' ? (
        <div className="min-h-0 flex-1 overflow-y-auto"><RecentNotifications /></div>
      ) : inboxLoaded && inbox.length === 0 ? (
        <div className="grid min-h-0 flex-1 place-items-center px-6 text-center">
          <div>
            <div className="text-[15px] font-medium">{tr('ui.inbox.youReAllCaughtUp')}</div>
            <p className="mt-1 text-[13px] text-fg-dim">{tr('ui.inbox.whenATaskPausesOn')}</p>
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <aside
            aria-label={tr('ui.inbox.waitingOnYou')}
            className={cn('flex w-[320px] flex-none flex-col border-r border-line bg-panel max-md:w-full max-md:border-r-0', selectedId && 'max-md:hidden')}
          >
            <ul className="flex-1 overflow-y-auto p-2">
              {!inboxLoaded && <LoadingState label={tr('ui.inbox.loadingInbox')} />}
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
            {selected && (
              <div className="mx-auto max-w-[760px]">
                <button type="button" onClick={() => setSelectedId(null)} className="mb-4 text-[13px] text-fg-dim hover:text-fg md:hidden">
                  {tr('ui.inbox.allItems')}
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
            )}
          </section>
        </div>
      )}
    </div>
  );
}
