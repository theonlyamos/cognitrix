import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Pin as PinIcon, Settings2 as SettingsIcon } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Select } from '@/lib/components/ui/select';
import { PageHeader, LoadingState, ErrorState, EmptyState, FilterChips } from '@/components/list-ui';
import { timeAgo } from '@/lib/agentDraft';
import { ENDED, type Swarm } from '@/lib/swarms';
import {
  BOARD_KINDS, kindLabel, kindTone, type BoardFollow, type BoardInfo, type BoardKind, type BoardPost,
  type BoardSettings,
} from '@/lib/board';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';
import { MentionText, MentionTextarea } from '@/components/board/Mentions';

type Filter = 'all' | 'open' | BoardKind;

const field = 'h-10 rounded-lg border border-line bg-panel px-3 text-[13.5px] outline-none placeholder:text-fg-dim focus:border-fg-dim md:h-9';
const small = 'inline-flex min-h-11 items-center gap-1 rounded-md border border-line px-2.5 text-[12px] text-fg hover:bg-panel-2 disabled:opacity-50 md:min-h-7';

function whoLabel(name: string): string {
  return name === 'you' ? tr('ui.board.you') : name === 'lead' ? tr('ui.board.theLead') : name;
}

function statusText(post: BoardPost): string {
  if (post.status === 'open' && post.to_name === 'you') return tr('ui.board.openForYou');
  if (post.status === 'open') return post.to_name ? tr('ui.board.openForName', { name: whoLabel(post.to_name) }) : tr('ui.board.openForAnyone');
  if (post.status === 'answered') return tr('ui.board.answered');
  if (post.status === 'done') return tr('ui.board.done');
  return '';
}

function tagged(list?: { name: string }[]): string[] {
  return (list || []).map((m) => m.name).filter(Boolean);
}

/** One post with its answers and what can be done with it. */
function PostCard({ post, highlight, names, onChanged }: {
  post: BoardPost; highlight: boolean; names: string[]; onChanged: () => void;
}) {
  const navigate = useNavigate();
  const [answering, setAnswering] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(post.text);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const card = useRef<HTMLElement>(null);
  useEffect(() => { if (highlight) card.current?.scrollIntoView({ block: 'center' }); }, [highlight]);
  const ask = post.kind === 'question' || post.kind === 'request';
  const mine = post.author_kind === 'person';
  const retracted = !!post.retracted;

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setError('');
    try {
      await fn();
      onChanged();
      return true;
    } catch (e) {
      setError(errorMessage(e, tr('ui.board.thatDidnTWork')));
      return false;
    } finally {
      setBusy('');
    }
  };
  const send = async (event: FormEvent, close: boolean) => {
    event.preventDefault();
    if (!text.trim()) return;
    if (await act('reply', () => api.post(`/board/posts/${post.id}/reply`, { text, close }))) {
      setText('');
      setAnswering(false);
    }
  };

  const saveEdit = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft.trim()) return;
    if (await act('edit', () => api.patch(`/board/posts/${post.id}`, { text: draft }))) setEditing(false);
  };

  return (
    <article
      ref={card}
      aria-label={tr('ui.board.kindByAuthor', { kind: kindLabel(post.kind), author: post.author_name })}
      className={cn('flex flex-col gap-2 rounded-xl border bg-panel px-3.5 py-3 text-[13.5px]',
        highlight ? 'border-accent' : 'border-line', retracted && 'opacity-60')}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium">{post.author_kind === 'person' ? tr('ui.board.you') : post.author_name}</span>
        <span className={cn('rounded px-1.5 py-px font-mono text-[10px] font-semibold uppercase tracking-wide', kindTone(post.kind))}>
          {kindLabel(post.kind)}
        </span>
        {post.to_name && post.status !== 'open' && (
          <span className="text-[12px] text-fg-dim">{tr('ui.board.toName', { name: whoLabel(post.to_name) })}</span>
        )}
        {post.pinned && <PinIcon size={12} aria-label={tr('ui.board.pinned')} className="text-fg-dim" />}
        <span className={cn('ml-auto text-[12px]', retracted ? 'text-fg-dim' : post.status === 'open' ? 'text-warn-ink' : post.status ? 'text-ok' : 'text-fg-dim')}>
          {retracted ? tr('ui.board.withdrawn') : statusText(post)}
        </span>
      </div>
      {editing ? (
        <form onSubmit={(e) => void saveEdit(e)} className="flex flex-col gap-2">
          <MentionTextarea value={draft} onValue={setDraft} names={names} rows={3} maxLength={2000} autoFocus
            aria-label={tr('ui.board.editPost')}
            className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-[13.5px] outline-none focus:border-fg-dim" />
          <div className="flex flex-wrap gap-1.5">
            <Button type="submit" size="sm" disabled={!draft.trim() || busy === 'edit'}>{tr('ui.board.save')}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft(post.text); }}>{tr('ui.board.cancel')}</Button>
          </div>
        </form>
      ) : (
        <p className={cn('whitespace-pre-wrap leading-relaxed [overflow-wrap:anywhere]', retracted && 'line-through')}>
          <MentionText text={post.text} names={tagged(post.mentions)} />
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-fg-dim">
        {post.tags.map((t) => <span key={t} className="font-mono">#{t}</span>)}
        <span>{timeAgo(post.posted_at)}</span>
        {post.edited_at && !retracted && <span>{tr('ui.board.edited')}</span>}
        {post.wake?.state === 'sent' && post.wake.chat_id && (
          <button type="button" className="text-accent-ink hover:underline" onClick={() => navigate(`/home?chat=${encodeURIComponent(post.wake.chat_id || '')}`)}>
            {tr('ui.board.nameWasAskedToAnswer', { name: whoLabel(post.to_name) })}
          </button>
        )}
        {post.wake?.state === 'skipped' && post.wake.reason && <span>{post.wake.reason}</span>}
        {(post.mention_wakes || []).filter((w) => w.state === 'sent' && w.chat_id).map((w, i) => (
          <button key={i} type="button" className="text-accent-ink hover:underline"
            onClick={() => navigate(`/home?chat=${encodeURIComponent(w.chat_id || '')}`)}>
            {tr('ui.board.nameWasTagged', { name: w.name })}
          </button>
        ))}
      </div>
      {post.replies.length > 0 && (
        <ul className="flex flex-col gap-1.5 border-l-2 border-line pl-3">
          {post.replies.map((r, i) => (
            <li key={i} className="text-[13px] leading-relaxed">
              <span className="font-medium">{r.author_kind === 'person' ? tr('ui.board.you') : r.author_name}</span>{' '}
              <span className="text-fg-dim">{timeAgo(r.at)}</span>
              <p className="whitespace-pre-wrap text-fg-dim [overflow-wrap:anywhere]"><MentionText text={r.text} names={tagged(r.mentions)} /></p>
            </li>
          ))}
        </ul>
      )}
      {answering && (
        <form onSubmit={(e) => void send(e, false)} className="flex flex-col gap-2">
          <MentionTextarea
            value={text}
            onValue={setText}
            names={names}
            rows={3}
            maxLength={2000}
            autoFocus
            aria-label={tr('ui.board.yourAnswer')}
            placeholder={tr('ui.board.writeYourAnswerTag')}
            className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-[13.5px] outline-none focus:border-fg-dim"
          />
          <div className="flex flex-wrap gap-1.5">
            <Button type="submit" size="sm" disabled={!text.trim() || busy === 'reply'}>{tr('ui.board.answer')}</Button>
            {ask && post.status !== 'done' && (
              <Button type="button" size="sm" variant="outline" disabled={!text.trim() || busy === 'reply'} onClick={(e) => void send(e, true)}>
                {tr('ui.board.answerAndClose')}
              </Button>
            )}
            <Button type="button" size="sm" variant="ghost" onClick={() => setAnswering(false)}>{tr('ui.board.cancel')}</Button>
          </div>
        </form>
      )}
      {!answering && !editing && (
        <div className="flex flex-wrap gap-1.5">
          {!retracted && (
            <button type="button" className={small} onClick={() => setAnswering(true)}>{ask ? tr('ui.board.answer') : tr('ui.board.reply')}</button>
          )}
          {mine && !retracted && (
            <button type="button" className={small} onClick={() => { setDraft(post.text); setEditing(true); }}>{tr('ui.board.edit')}</button>
          )}
          {mine && !retracted && (
            <button type="button" className={small} disabled={busy === 'retract'}
              onClick={() => void act('retract', () => api.post(`/board/posts/${post.id}/retract`, {}))}>
              {tr('ui.board.withdraw')}
            </button>
          )}
          {!retracted && ask && post.status !== 'done' && (
            <button type="button" className={small} disabled={busy === 'status'}
              onClick={() => void act('status', () => api.post(`/board/posts/${post.id}/status`, { status: 'done' }))}>
              {tr('ui.board.markDone')}
            </button>
          )}
          {!retracted && ask && post.status === 'done' && (
            <button type="button" className={small} disabled={busy === 'status'}
              onClick={() => void act('status', () => api.post(`/board/posts/${post.id}/status`, { status: 'open' }))}>
              {tr('ui.board.reopen')}
            </button>
          )}
          {!retracted && <button type="button" className={small} disabled={busy === 'pin'}
            onClick={() => void act('pin', () => api.post(`/board/posts/${post.id}/pin`, { pinned: !post.pinned }))}>
            {post.pinned ? tr('ui.board.unpin') : tr('ui.board.pin')}
          </button>}
          <button type="button" className={cn(small, 'text-danger-ink')} disabled={busy === 'delete'}
            onClick={() => void act('delete', () => api.delete(`/board/posts/${post.id}`))}>
            {tr('ui.board.delete')}
          </button>
        </div>
      )}
      {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
    </article>
  );
}

/** Post as the person. */
function Composer({ board, targets, names, onPosted }: {
  board: string; targets: string[]; names: string[]; onPosted: () => void;
}) {
  const [kind, setKind] = useState<BoardKind>('heads_up');
  const [text, setText] = useState('');
  const [to, setTo] = useState('');
  const [tags, setTags] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setTo(''); }, [board]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError('');
    try {
      await api.post('/board/posts', { board, kind, text, to, tags });
      setText('');
      setTags('');
      onPosted();
    } catch (e) {
      setError(errorMessage(e, tr('ui.board.couldNotPost')));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 rounded-xl border border-line bg-panel px-3.5 py-3">
      <MentionTextarea
        value={text}
        onValue={setText}
        names={names}
        rows={2}
        maxLength={2000}
        aria-label={tr('ui.board.newPost')}
        placeholder={tr('ui.board.postSomethingTagAgents')}
        className="w-full resize-y bg-transparent text-[13.5px] leading-relaxed outline-none placeholder:text-fg-dim"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Select value={kind} onChange={(e) => setKind(e.target.value as BoardKind)} aria-label={tr('ui.board.kind')} className="h-9 w-auto">
          {BOARD_KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
        </Select>
        <Select value={to} onChange={(e) => setTo(e.target.value)} aria-label={tr('ui.board.for')} className="h-9 w-auto max-w-[180px]">
          <option value="">{tr('ui.board.forAnyone')}</option>
          {targets.map((name) => <option key={name} value={name}>{whoLabel(name)}</option>)}
        </Select>
        <input value={tags} onChange={(e) => setTags(e.target.value)} aria-label={tr('ui.board.tags')}
          placeholder={tr('ui.board.tagsCommaSeparated')} className={cn(field, 'w-40 min-w-0 flex-1 sm:flex-none')} />
        <Button type="submit" size="sm" className="ml-auto" disabled={busy || !text.trim()}>{tr('ui.board.post')}</Button>
      </div>
      {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
    </form>
  );
}

/** On/off, wakes a day and how long posts stay. */
function SettingsPanel({ onClose }: { onClose: () => void }) {
  const { data, setData } = useResource<BoardSettings>('/board/settings');
  const [error, setError] = useState('');
  const save = async (changes: Partial<BoardSettings>) => {
    setError('');
    try {
      const res = await api.put<BoardSettings>('/board/settings', changes);
      setData(res.data);
    } catch (e) {
      setError(errorMessage(e, tr('ui.board.couldNotSaveTheSettings')));
    }
  };
  if (!data) return null;
  return (
    <section aria-label={tr('ui.board.boardSettings')} className="flex flex-col gap-3 rounded-xl border border-line bg-panel px-3.5 py-3 text-[13px]">
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={data.enabled} onChange={(e) => void save({ enabled: e.target.checked })} />
        {tr('ui.board.agentsUseTheMessageBoard')}
      </label>
      <label className="flex flex-wrap items-center gap-2">
        {tr('ui.board.chatsTheBoardMayStartADay')}
        <input type="number" min={0} max={100} defaultValue={data.wakes_per_day} aria-label={tr('ui.board.chatsADay')}
          onBlur={(e) => void save({ wakes_per_day: Number(e.target.value) })} className={cn(field, 'w-20')} />
        <span className="text-fg-dim">{tr('ui.board.wakesTodayUsedToday', { count: data.wakes_today })}</span>
      </label>
      <label className="flex flex-wrap items-center gap-2">
        {tr('ui.board.keepPostsFor')}
        <input type="number" min={1} max={365} defaultValue={data.keep_days} aria-label={tr('ui.board.daysToKeepPosts')}
          onBlur={(e) => void save({ keep_days: Number(e.target.value) })} className={cn(field, 'w-20')} />
        {tr('ui.board.daysPinnedAndOpenPostsStay')}
      </label>
      <label className="flex flex-wrap items-center gap-2">
        {tr('ui.board.bringUpUnansweredQuestionsAfter')}
        <input type="number" min={0} max={336} defaultValue={data.nudge_hours} aria-label={tr('ui.board.hoursBeforeBringingUp')}
          onBlur={(e) => void save({ nudge_hours: Number(e.target.value) })} className={cn(field, 'w-20')} />
        {tr('ui.board.hoursZeroNever')}
      </label>
      {error && <p role="alert" className="text-danger-ink">{error}</p>}
      <div><Button type="button" size="sm" variant="outline" onClick={onClose}>{tr('ui.board.close')}</Button></div>
    </section>
  );
}

/** What each agent hears about at the start of a turn. */
function Follows() {
  const { data, setData } = useResource<BoardFollow[]>('/board/follows');
  const [editing, setEditing] = useState('');
  const save = async (row: BoardFollow, changes: Partial<Omit<BoardFollow, 'tags'>> & { tags?: string[] | string }) => {
    const res = await api.put<BoardFollow>(`/board/follows/${row.agent_id}`, changes);
    setData((rows) => (rows || []).map((r) => (r.agent_id === row.agent_id ? res.data : r)));
  };
  if (!data || data.length === 0) return null;
  return (
    <section aria-label={tr('ui.board.whatAgentsHearAbout')} className="flex flex-col gap-2">
      <h2 className="text-[13px] font-semibold">{tr('ui.board.whatAgentsHearAbout')}</h2>
      <p className="text-[12px] leading-snug text-fg-dim">{tr('ui.board.questionsAndRequestsForAnAgent')}</p>
      <ul className="flex flex-col gap-1.5">
        {data.map((row) => (
          <li key={row.agent_id} className="text-[12.5px]">
            <button type="button" onClick={() => setEditing(editing === row.agent_id ? '' : row.agent_id)}
              aria-expanded={editing === row.agent_id} className="flex w-full items-baseline justify-between gap-2 text-left">
              <span className="truncate font-medium">{row.agent_name}</span>
              <span className="truncate text-[11.5px] text-fg-dim">
                {[...row.kinds.map(kindLabel), ...row.tags.map((t) => `#${t}`)].join(', ') || tr('ui.board.nothingNew')}
              </span>
            </button>
            {editing === row.agent_id && (
              <div className="mt-1.5 flex flex-col gap-1.5 rounded-lg border border-line px-2.5 py-2">
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  {BOARD_KINDS.map((k) => (
                    <label key={k} className="flex items-center gap-1">
                      <input type="checkbox" checked={row.kinds.includes(k)}
                        onChange={(e) => void save(row, { kinds: e.target.checked ? [...row.kinds, k] : row.kinds.filter((x) => x !== k) })} />
                      {kindLabel(k)}
                    </label>
                  ))}
                </div>
                <input defaultValue={row.tags.join(', ')} aria-label={tr('ui.board.tagsToHearAbout')}
                  placeholder={tr('ui.board.tagsToHearAbout')}
                  onBlur={(e) => void save(row, { tags: e.target.value })} className={cn(field, 'h-9 w-full')} />
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={row.wake} onChange={(e) => void save(row, { wake: e.target.checked })} />
                  {tr('ui.board.aQuestionForItStartsAChat')}
                </label>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The swarm's jobs beside its board. */
function SwarmJobs({ swarmId }: { swarmId: string }) {
  const navigate = useNavigate();
  const { data } = useResource<Swarm>(`/swarms/${swarmId}`);
  if (!data) return null;
  const ended = data.workers.filter((w) => ENDED.includes(w.status)).length;
  return (
    <section aria-label={tr('ui.board.swarmJobs')} className="flex flex-col gap-2">
      <h2 className="text-[13px] font-semibold">{tr('ui.board.swarmJobs')}</h2>
      <div className="h-1 overflow-hidden rounded-full bg-panel-2">
        <div className="h-full rounded-full bg-accent" style={{ width: `${data.workers.length ? Math.round((ended / data.workers.length) * 100) : 0}%` }} />
      </div>
      <ul className="flex flex-col gap-1 text-[12.5px]">
        {data.workers.map((w) => (
          <li key={w.id} className="flex items-baseline justify-between gap-2">
            {w.chat_id ? (
              <button type="button" className="truncate text-left hover:underline" onClick={() => navigate(`/home?chat=${encodeURIComponent(w.chat_id)}`)}>{w.name}</button>
            ) : <span className="truncate">{w.name}</span>}
            <span className="flex-none text-fg-dim">{w.status}</span>
          </li>
        ))}
      </ul>
      {data.session_id && (
        <Link to={`/home?chat=${encodeURIComponent(data.session_id)}`} className="text-[12px] text-accent-ink hover:underline">
          {tr('ui.board.openTheLeadSChat')}
        </Link>
      )}
    </section>
  );
}

/** The message board agents share, and each swarm's own board. */
export default function Board() {
  const [params, setParams] = useSearchParams();
  const board = params.get('board') || 'agents';
  const highlight = params.get('post') || '';
  const [filter, setFilter] = useState<Filter>('all');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => { setSearch(''); setQuery(''); }, [board]);
  const boards = useResource<BoardInfo[]>('/board/boards');
  const posts = useResource<BoardPost[]>(
    `/board/posts?board=${encodeURIComponent(board)}${query ? `&q=${encodeURIComponent(query)}` : ''}`);
  const mentionable = useResource<{ name: string }[]>(`/board/mentions?board=${encodeURIComponent(board)}`);
  const names = useMemo(() => (mentionable.data || []).map((m) => m.name), [mentionable.data]);
  const follows = useResource<BoardFollow[]>(board === 'agents' ? '/board/follows' : null);
  const current = boards.data?.find((b) => b.key === board);
  const swarmId = board.startsWith('swarm:') ? board.slice(6) : '';
  const swarm = useResource<Swarm>(swarmId ? `/swarms/${swarmId}` : null);
  const { refetch: refetchPosts } = posts;
  const { refetch: refetchBoards } = boards;

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refetchPosts({ silent: true });
      void refetchBoards({ silent: true });
    }, 8000);
    return () => window.clearInterval(timer);
  }, [refetchPosts, refetchBoards]);

  const changed = () => {
    void refetchPosts({ silent: true });
    void refetchBoards({ silent: true });
  };
  const pick = (key: string) => setParams(key === 'agents' ? {} : { board: key });

  const listed = useMemo(() => posts.data || [], [posts.data]);
  const missing = !query && !!highlight && !!posts.data && !listed.some((p) => p.id === highlight);
  const linked = useResource<BoardPost>(missing ? `/board/posts/${encodeURIComponent(highlight)}` : null);
  const all = useMemo(
    () => (missing && linked.data && linked.data.board === board ? [linked.data, ...listed] : listed),
    [missing, linked.data, listed, board],
  );
  const shown = all.filter((p) => filter === 'all' || (filter === 'open' ? p.status === 'open' : p.kind === filter));
  const pinned = filter === 'all' && !query ? shown.filter((p) => p.pinned) : [];
  const rest = filter === 'all' && !query ? shown.filter((p) => !p.pinned) : shown;
  const count = (f: Filter) => all.filter((p) => (f === 'open' ? p.status === 'open' : p.kind === f)).length;
  const targets = swarmId
    ? ['lead', ...(swarm.data?.workers.map((w) => w.name) || [])]
    : (follows.data || []).map((f) => f.agent_name);
  const title = board === 'agents' ? tr('ui.board.allAgents') : current?.title || tr('ui.board.swarmBoard');

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.board.messageBoard')} subtitle={tr('ui.board.whereYourAgentsShareFindings')}>
        <Button type="button" size="sm" variant="outline" aria-expanded={settingsOpen} onClick={() => setSettingsOpen((v) => !v)}>
          <SettingsIcon size={14} aria-hidden className="mr-1.5" />{tr('ui.board.settings')}
        </Button>
      </PageHeader>
      <div className="flex min-h-0 flex-1">
        <nav aria-label={tr('ui.board.boards')} className="hidden w-56 flex-none flex-col gap-1 overflow-y-auto border-r border-line px-2.5 py-4 md:flex">
          <BoardList boards={boards.data || []} current={board} onPick={pick} />
        </nav>
        <div className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-3xl flex-col gap-3 px-4 py-4 md:px-6">
            <div className="md:hidden">
              <Select value={board} onChange={(e) => pick(e.target.value)} aria-label={tr('ui.board.board')} className="h-10 w-full">
                {(boards.data || [{ key: 'agents', title: '', kind: 'agents' } as BoardInfo]).map((b) => (
                  <option key={b.key} value={b.key}>{b.kind === 'agents' ? tr('ui.board.allAgents') : b.title}</option>
                ))}
              </Select>
            </div>
            {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
            <div className="flex flex-col gap-0.5">
              <h2 className="text-[17px] font-semibold">{title}</h2>
              <p className="text-[12.5px] text-fg-dim">
                {swarmId
                  ? tr('ui.board.workersAndTheLeadOfThisSwarm', { name: current?.agent_name || swarm.data?.agent_name || '' })
                  : tr('ui.board.everyAgentOfYoursReadsAndPosts')}
              </p>
            </div>
            <Composer board={board} targets={targets} names={names} onPosted={changed} />
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)}
              aria-label={tr('ui.board.searchTheBoard')} placeholder={tr('ui.board.searchTheBoard')}
              className={cn(field, 'w-full')} />
            <FilterChips<Filter>
              label={tr('ui.board.show')}
              value={filter}
              onChange={setFilter}
              options={[
                { id: 'all', label: tr('ui.board.all') },
                { id: 'open', label: tr('ui.board.open'), count: count('open') },
                ...BOARD_KINDS.map((k) => ({ id: k as Filter, label: kindLabel(k), count: count(k) })),
              ]}
            />
            {posts.loading && !posts.data ? (
              <LoadingState label={tr('ui.board.loadingPosts')} />
            ) : posts.error ? (
              <ErrorState message={posts.error} onRetry={posts.refetch} />
            ) : shown.length === 0 ? (
              <EmptyState
                icon={<span aria-hidden className="font-mono text-lg">#</span>}
                title={query ? tr('ui.board.nothingMatches') : all.length ? tr('ui.board.nothingHere') : tr('ui.board.noPostsYet')}
                desc={query ? tr('ui.board.tryOtherWords') : all.length ? tr('ui.board.tryAnotherFilter') : tr('ui.board.agentsPostFindingsQuestionsAndDecisions')}
              />
            ) : (
              <div className="flex flex-col gap-2.5">
                {[...pinned, ...rest].map((p) => (
                  <PostCard key={p.id} post={p} highlight={p.id === highlight} names={names} onChanged={changed} />
                ))}
              </div>
            )}
            <div className="flex flex-col gap-4 border-t border-line pt-4 lg:hidden">
              {swarmId ? <SwarmJobs swarmId={swarmId} /> : <Follows />}
            </div>
          </div>
        </div>
        <aside className="hidden w-64 flex-none flex-col gap-5 overflow-y-auto border-l border-line px-4 py-4 lg:flex">
          {swarmId && <SwarmJobs swarmId={swarmId} />}
          <section aria-label={tr('ui.board.onThisBoard')} className="flex flex-col gap-1.5 text-[12.5px]">
            <h2 className="text-[13px] font-semibold">{tr('ui.board.onThisBoard')}</h2>
            <Row label={tr('ui.board.openQuestions')} value={all.filter((p) => p.kind === 'question' && p.status === 'open').length} />
            <Row label={tr('ui.board.openRequests')} value={all.filter((p) => p.kind === 'request' && p.status === 'open').length} />
            <Row label={tr('ui.board.findings')} value={count('finding')} />
            <Row label={tr('ui.board.decisions')} value={count('decision')} />
          </section>
          {!swarmId && <Follows />}
        </aside>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex justify-between gap-2">
      <span>{label}</span>
      <span className="font-mono text-fg-dim">{value}</span>
    </div>
  );
}

function BoardList({ boards, current, onPick }: { boards: BoardInfo[]; current: string; onPick: (key: string) => void }) {
  const everyone = boards.find((b) => b.kind === 'agents');
  const swarms = boards.filter((b) => b.kind === 'swarm');
  const item = (b: BoardInfo, label: string) => (
    <button
      key={b.key}
      type="button"
      aria-current={b.key === current ? 'page' : undefined}
      onClick={() => onPick(b.key)}
      className={cn('flex min-h-9 items-center justify-between gap-2 rounded-lg px-2.5 text-left text-[13px]',
        b.key === current ? 'bg-panel font-medium text-fg' : 'text-fg-dim hover:bg-panel hover:text-fg')}
    >
      <span className="truncate">{label}</span>
      {b.open > 0 && <span className="flex-none rounded-full bg-accent px-1.5 font-mono text-[10px] text-accent-foreground">{b.open}</span>}
    </button>
  );
  return (
    <>
      <div className="px-2.5 pb-1 text-[10.5px] font-semibold uppercase tracking-wider text-fg-dim">{tr('ui.board.boards')}</div>
      {item(everyone || { key: 'agents', kind: 'agents', title: '', status: '', open: 0, total: 0, latest: '' }, tr('ui.board.allAgents'))}
      {swarms.length > 0 && <div className="px-2.5 pb-1 pt-3 text-[10.5px] font-semibold uppercase tracking-wider text-fg-dim">{tr('ui.board.swarms')}</div>}
      {swarms.map((b) => item(b, b.title))}
    </>
  );
}
