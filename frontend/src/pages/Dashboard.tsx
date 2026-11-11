import { useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  ArrowUp,
  Bot,
  CheckCircle2,
  CircleDashed,
  Clock3,
  Coins,
  Inbox as InboxIcon,
  ListChecks,
  MessageSquare,
  Plus,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import { useUser } from '@/context/AppContext';
import { useOptionalNotifications, type InboxItem } from '@/context/NotificationsContext';
import { useResource } from '@/hooks/useResource';
import { usePolling } from '@/hooks/usePolling';
import { Button } from '@/lib/components/ui/button';
import { AgentBadge } from '@/components/StartChat';
import { formatCost, formatTokens } from '@/components/settings/Usage';
import { parseUtc, timeAgo, timeUntil } from '@/lib/agentDraft';
import { CHAT_DRAFT_KEY } from '@/lib/chatDraft';
import { MicButton, VoiceBar, VoiceNotice, useVoiceInput } from '@/components/VoiceInput';
import { insertTranscript } from '@/lib/voice';
import { cn } from '@/lib/utils';

interface Agent {
  id: string;
  name: string;
  llm?: { provider?: string | null; model?: string | null } | null;
}

interface ChatRow {
  id: string;
  title: string;
  updated_at: string | null;
  message_count: number;
  kind: 'agent' | 'team';
  agent_id: string | null;
  team_id: string | null;
  running: boolean;
  archived?: boolean;
}

interface TaskRow {
  id: string;
  title: string;
  status?: string;
  run_status?: string | null;
  run_started_at?: string | null;
  run_completed_at?: string | null;
}

interface AutomationRow {
  id: string;
  title: string;
  schedule_enabled: boolean;
  schedule_interval: number | null;
  schedule_cron: string | null;
  schedule_at: string | null;
  next_run_at: string | null;
  wake_pending: boolean;
  wake_at: string | null;
  triggers: { enabled: boolean }[];
}

interface Totals {
  prompt_tokens: number;
  completion_tokens: number;
  cost: number;
}
interface UsageSummary {
  prices_available: boolean;
  total: Totals;
  by_day: (Totals & { day: string })[];
  by_model: (Totals & { provider: string; model: string })[];
}

const ACTIVE_RUN = new Set(['queued', 'running', 'cancelling']);
const tokensOf = (t: Totals) => t.prompt_tokens + t.completion_tokens;

function greeting(hour: number): string {
  if (hour < 5) return 'Working late';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/** The next time an automation starts on its own (schedule or self-check). */
function nextAt(a: AutomationRow): string | null {
  const times = [
    a.schedule_enabled ? a.next_run_at : null,
    a.wake_pending ? a.wake_at : null,
  ].filter((t): t is string => !!t);
  if (!times.length) return null;
  return times.sort((x, y) => (parseUtc(x) ?? 0) - (parseUtc(y) ?? 0))[0];
}

function every(seconds: number): string {
  if (seconds === 86400) return 'daily';
  if (seconds === 3600) return 'hourly';
  if (seconds === 604800) return 'weekly';
  if (seconds % 86400 === 0) return `every ${seconds / 86400} d`;
  if (seconds % 3600 === 0) return `every ${seconds / 3600} h`;
  if (seconds % 60 === 0) return `every ${seconds / 60} min`;
  return `every ${seconds} s`;
}

function howItRuns(a: AutomationRow): string {
  if (a.schedule_cron) return `cron ${a.schedule_cron}`;
  if (a.schedule_interval) return every(a.schedule_interval);
  if (a.schedule_at) return 'runs once';
  if (a.wake_pending) return 'agent checks back';
  if (a.triggers.some((t) => t.enabled)) return 'on webhook';
  return 'paused';
}

function inboxTitle(item: InboxItem): string {
  if (item.kind === 'approval') return `Run ${item.tool_name}`;
  if (item.kind === 'notification') return item.title;
  return String(item.question.prompt || 'An agent asked a question');
}

function inboxSource(item: InboxItem): string {
  if (item.kind === 'approval') return item.source === 'chat' ? `${item.agent_name || 'Agent'} · chat` : item.task_title || 'Task run';
  if (item.kind === 'notification') return item.task_title || 'Agent';
  return 'Chat';
}

function inboxHref(item: InboxItem): string {
  if (item.kind === 'notification' && item.link) return item.link;
  if (item.kind === 'approval' && item.href) return item.href;
  return '/inbox';
}

const KIND_STYLE: Record<InboxItem['kind'], { label: string; className: string }> = {
  approval: { label: 'APPROVAL', className: 'bg-[#ffb86b]/15 text-[#b4691a] dark:text-[#ffb86b]' },
  question: { label: 'QUESTION', className: 'bg-[#7cc4ff]/15 text-[#1f6f8b] dark:text-[#7cc4ff]' },
  notification: { label: 'ALERT', className: 'bg-accent/15 text-accent-ink' },
};

/* ------------------------------------------------------------------ pieces */

function Card({
  title,
  icon: Icon,
  action,
  children,
  className,
}: {
  title: string;
  icon: LucideIcon;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('flex min-w-0 flex-col rounded-[14px] border border-line bg-panel', className)}>
      <header className="flex min-h-12 items-center gap-2.5 border-b border-line px-4">
        <Icon className="h-[15px] w-[15px] flex-none text-fg-dim" strokeWidth={1.9} aria-hidden="true" />
        <h2 className="flex-1 text-[13.5px] font-semibold tracking-tight">{title}</h2>
        {action}
      </header>
      <div className="flex-1">{children}</div>
    </section>
  );
}

function CardLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="group inline-flex items-center gap-1 rounded px-1.5 py-1 font-mono text-[11px] text-fg-dim transition-colors hover:text-fg"
    >
      {children}
      <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
    </Link>
  );
}

function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 p-4" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <span className="h-7 w-7 flex-none animate-pulse rounded-md bg-panel-2" />
          <span className="h-3 flex-1 animate-pulse rounded bg-panel-2" style={{ maxWidth: `${70 - i * 12}%` }} />
        </div>
      ))}
    </div>
  );
}

function Quiet({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <div className="flex items-center gap-3 px-4 py-6 text-[13px] text-fg-dim">
      <Icon className="h-[18px] w-[18px] flex-none" strokeWidth={1.8} aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}

function LiveDot({ className }: { className?: string }) {
  return (
    <span className={cn('relative inline-flex h-2 w-2 flex-none', className)} aria-hidden="true">
      <span className="absolute inset-0 animate-ping rounded-full bg-accent opacity-70 motion-reduce:hidden" />
      <span className="relative h-2 w-2 rounded-full bg-accent" />
    </span>
  );
}

function Sparkline({ values, label }: { values: number[]; label: string }) {
  const peak = Math.max(...values, 0);
  const w = 96;
  const h = 34;
  if (!values.length) return null;
  const step = values.length > 1 ? w / (values.length - 1) : w;
  const points = values.map((v, i) => [i * step, h - 2 - (peak ? (v / peak) * (h - 6) : 0)] as const);
  const line = points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-[34px] w-[96px] overflow-visible" role="img" aria-label={label}>
      <defs>
        <linearGradient id="dash-spark" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.35" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${w},${h} L0,${h} Z`} fill="url(#dash-spark)" />
      <path d={line} fill="none" stroke="var(--accent-ink)" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function StatTile({
  to,
  label,
  icon: Icon,
  value,
  detail,
  tone = 'plain',
  live = false,
  extra,
}: {
  to: string;
  label: string;
  icon: LucideIcon;
  value: ReactNode;
  detail: ReactNode;
  tone?: 'plain' | 'warn';
  live?: boolean;
  extra?: ReactNode;
}) {
  return (
    <Link
      to={to}
      className={cn(
        'group relative flex min-w-0 flex-col justify-between gap-3 overflow-hidden rounded-[14px] border bg-panel p-4 transition-colors hover:border-fg-dim/50',
        tone === 'warn' ? 'border-[#ffb86b]/50' : 'border-line',
      )}
    >
      {tone === 'warn' && <span aria-hidden="true" className="pointer-events-none absolute inset-0 bg-gradient-to-br from-[#ffb86b]/[0.09] to-transparent" />}
      <div className="relative flex items-center justify-between gap-2">
        <span className="font-mono text-[10.5px] tracking-[0.13em] text-fg-dim">{label}</span>
        <span
          className={cn(
            'grid h-7 w-7 flex-none place-items-center rounded-md border',
            tone === 'warn' ? 'border-[#ffb86b]/40 text-[#b4691a] dark:text-[#ffb86b]' : 'border-line text-fg-dim group-hover:text-fg',
          )}
        >
          <Icon className="h-[15px] w-[15px]" strokeWidth={1.9} aria-hidden="true" />
        </span>
      </div>
      <div className="relative flex items-end justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[28px] font-semibold leading-none tracking-tight tabular-nums">
            {value}
            {live && <LiveDot />}
          </div>
          <div className="mt-1.5 truncate text-[12px] text-fg-dim">{detail}</div>
        </div>
        {extra}
      </div>
    </Link>
  );
}

/* ------------------------------------------------------------------- page */

export default function Dashboard() {
  const navigate = useNavigate();
  const { user } = useUser();
  const notifications = useOptionalNotifications();
  const inbox = notifications?.inbox ?? [];
  const inboxLoaded = notifications?.inboxLoaded ?? true;

  const agents = useResource<Agent[]>('/agents');
  const chats = useResource<ChatRow[]>('/sessions/chats');
  const tasks = useResource<TaskRow[]>('/tasks');
  const automations = useResource<AutomationRow[]>('/automations');
  const runs = useResource<{ runs: { session_id: string; agent_id: string }[] }>('/agents/runs');
  const usage = useResource<UsageSummary>('/usage?days=14');

  usePolling(() => {
    void chats.refetch({ silent: true });
    void tasks.refetch({ silent: true });
    void runs.refetch({ silent: true });
    void automations.refetch({ silent: true });
  }, 15000, true);

  const now = Date.now();
  const agentList = useMemo(() => agents.data ?? [], [agents.data]);
  const agentById = useMemo(() => new Map(agentList.map((a) => [a.id, a])), [agentList]);
  const chatList = (chats.data ?? []).filter((c) => !c.archived);
  const taskList = tasks.data ?? [];
  const automationList = automations.data ?? [];
  const activeChatRuns = runs.data?.runs ?? [];

  const runningTasks = taskList.filter((t) => t.run_status && ACTIVE_RUN.has(t.run_status));
  const workingAgents = new Set(activeChatRuns.map((r) => r.agent_id));
  const approvals = inbox.filter((i) => i.kind === 'approval').length;
  const questions = inbox.filter((i) => i.kind === 'question').length;

  const upcoming = automationList
    .map((a) => ({ a, at: nextAt(a) }))
    .sort((x, y) => (parseUtc(x.at) ?? Infinity) - (parseUtc(y.at) ?? Infinity));
  const nextRun = upcoming.find((u) => u.at)?.at ?? null;

  const recentTasks = [...taskList]
    .filter((t) => t.run_status)
    .sort((x, y) => (parseUtc(y.run_completed_at || y.run_started_at) ?? 0) - (parseUtc(x.run_completed_at || x.run_started_at) ?? 0))
    .slice(0, 5);

  const chatsPerAgent = new Map<string, number>();
  for (const c of chatList) if (c.agent_id) chatsPerAgent.set(c.agent_id, (chatsPerAgent.get(c.agent_id) ?? 0) + 1);
  const topAgents = [...agentList]
    .sort((x, y) => (chatsPerAgent.get(y.id) ?? 0) - (chatsPerAgent.get(x.id) ?? 0))
    .slice(0, 5);

  // Usage: this week against the week before.
  const days = usage.data?.by_day ?? [];
  const byCost = !!usage.data?.prices_available && (usage.data?.total.cost ?? 0) > 0;
  const amount = (t: Totals) => (byCost ? t.cost : tokensOf(t));
  const thisWeek = days.slice(-7).reduce((s, d) => s + amount(d), 0);
  const lastWeek = days.slice(-14, -7).reduce((s, d) => s + amount(d), 0);
  const change = lastWeek > 0 ? Math.round(((thisWeek - lastWeek) / lastWeek) * 100) : null;
  const fmt = (n: number) => (byCost ? formatCost(n) : formatTokens(n));

  // Quick ask: opens a fresh chat with the picked agent, the question typed in.
  const [ask, setAsk] = useState('');
  const askRef = useRef<HTMLInputElement>(null);
  // Voice: the transcript goes into the ask bar, ready to edit or send.
  const voice = useVoiceInput({
    onText: (text) => {
      setAsk((current) => insertTranscript(current, text).value);
      requestAnimationFrame(() => askRef.current?.focus());
    },
  });
  const [askAgent, setAskAgent] = useState(() => {
    try {
      return localStorage.getItem('selectedAgentId') || '';
    } catch {
      return '';
    }
  });
  const pickedAgent = agentById.get(askAgent) ? askAgent : agentList[0]?.id ?? '';
  const startAsk = (e: FormEvent) => {
    e.preventDefault();
    if (!pickedAgent) {
      navigate('/agents/new');
      return;
    }
    try {
      localStorage.setItem('selectedAgentId', pickedAgent);
      localStorage.setItem(`chatSession:${pickedAgent}`, '');
      if (ask.trim()) sessionStorage.setItem(CHAT_DRAFT_KEY, ask.trim());
    } catch {
      /* storage off: still open the chat */
    }
    navigate('/home');
  };

  const hour = new Date().getHours();
  const firstName = (user?.name || '').trim().split(/\s+/)[0];
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });

  const summary: string[] = [];
  if (inboxLoaded) summary.push(inbox.length ? `${plural(inbox.length, 'thing')} waiting on you` : 'nothing waiting on you');
  const working = workingAgents.size + runningTasks.length;
  if (working) summary.push(`${working} working right now`);
  if (nextRun) summary.push(`next automation ${timeUntil(nextRun, now)}`);

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[1180px] px-4 pb-12 pt-6 md:px-8 md:pt-9">
          {/* Hero */}
          <section className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
            <div className="min-w-0 pl-12 md:pl-0">
              <div className="font-mono text-[11px] uppercase tracking-[0.16em] text-fg-dim">{today}</div>
              <h1 className="mt-2 text-[26px] font-semibold leading-tight tracking-tight md:text-[32px]">
                {greeting(hour)}
                {firstName ? `, ${firstName}` : ''}
              </h1>
              {summary.length > 0 && <p className="mt-1.5 text-[14px] text-fg-dim">{summary.join(' · ')}</p>}
            </div>
            <div className="flex flex-none flex-wrap gap-2">
              <Button asChild variant="outline" size="sm">
                <Link to="/tasks/new">
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" /> New task
                </Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link to="/agents/new">
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" /> New agent
                </Link>
              </Button>
            </div>
          </section>

          {/* Quick ask */}
          <form
            onSubmit={startAsk}
            className="mt-6 flex items-center gap-2 rounded-[16px] border border-line bg-panel p-2 pl-4 shadow-[0_1px_0_rgba(0,0,0,0.02),0_8px_24px_-16px_rgba(0,0,0,0.25)] transition-colors focus-within:border-fg-dim/60"
          >
            {voice.active ? (
              <div className="min-w-0 flex-1">
                <VoiceBar voice={voice} />
              </div>
            ) : (
              <>
            <MessageSquare className="hidden h-[17px] w-[17px] flex-none text-fg-dim sm:block" strokeWidth={1.9} aria-hidden="true" />
            <label htmlFor="dash-ask" className="sr-only">
              Ask an agent
            </label>
            <input
              id="dash-ask"
              value={ask}
              onChange={(e) => setAsk(e.target.value)}
              ref={askRef}
              placeholder={agentList.length ? 'Ask anything, or hand off a job…' : 'Create an agent to start chatting'}
              className="min-w-0 flex-1 bg-transparent py-2 text-[15px] outline-none placeholder:text-fg-dim"
            />
                <MicButton voice={voice} className="flex-none" />
              </>
            )}
            {agentList.length > 0 && (
              <>
                <label htmlFor="dash-ask-agent" className="sr-only">
                  Agent
                </label>
                <select
                  id="dash-ask-agent"
                  value={pickedAgent}
                  onChange={(e) => setAskAgent(e.target.value)}
                  className="hidden h-9 max-w-[180px] flex-none truncate rounded-md border border-line bg-panel-2 px-2 font-mono text-[11.5px] text-fg outline-none sm:block"
                >
                  {agentList.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </>
            )}
            <button
              type="submit"
              aria-label={agentList.length ? 'Start chat' : 'Create an agent'}
              className="grid h-10 w-10 flex-none place-items-center rounded-[11px] bg-accent text-accent-foreground transition-[filter] hover:brightness-[1.06]"
            >
              <ArrowUp className="h-[18px] w-[18px]" strokeWidth={2.2} aria-hidden="true" />
            </button>
          </form>
          <VoiceNotice voice={voice} />

          {/* Stats */}
          <section aria-label="At a glance" className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              to="/inbox"
              label="WAITING ON YOU"
              icon={InboxIcon}
              value={inboxLoaded ? inbox.length : '–'}
              detail={inbox.length ? [approvals && plural(approvals, 'approval'), questions && plural(questions, 'question'), inbox.length - approvals - questions && plural(inbox.length - approvals - questions, 'alert')].filter(Boolean).join(' · ') : 'All clear'}
              tone={inbox.length ? 'warn' : 'plain'}
            />
            <StatTile
              to="/tasks"
              label="WORKING NOW"
              icon={Bot}
              value={runs.data || tasks.data ? working : '–'}
              live={working > 0}
              detail={working ? [workingAgents.size && plural(activeChatRuns.length, 'chat'), runningTasks.length && plural(runningTasks.length, 'task')].filter(Boolean).join(' · ') : 'Agents are idle'}
            />
            <StatTile
              to="/automations"
              label="AUTOMATIONS"
              icon={Zap}
              value={automations.data ? automationList.length : '–'}
              detail={nextRun ? `next ${timeUntil(nextRun, now)}` : automationList.length ? 'nothing scheduled' : 'none set up yet'}
            />
            <StatTile
              to="/settings/usage"
              label={byCost ? 'SPEND · 7 DAYS' : 'TOKENS · 7 DAYS'}
              icon={Coins}
              value={usage.data ? fmt(thisWeek) : '–'}
              detail={
                change === null ? (
                  'last 7 days'
                ) : (
                  <span>
                    <span className={cn('font-mono', change > 0 ? 'text-[#b4691a] dark:text-[#ffb86b]' : 'text-ok')}>
                      {change > 0 ? '▲' : '▼'} {Math.abs(change)}%
                    </span>{' '}
                    vs last wk
                  </span>
                )
              }
              extra={
                days.length > 1 ? (
                  <span className="hidden sm:block">
                    <Sparkline values={days.map(amount)} label={`${byCost ? 'Spend' : 'Tokens'} per day, last 14 days`} />
                  </span>
                ) : null
              }
            />
          </section>

          {/* Main grid */}
          <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
            <div className="flex min-w-0 flex-col gap-4">
              {/* Needs you */}
              <Card title="Needs you" icon={InboxIcon} action={inbox.length > 0 ? <CardLink to="/inbox">Open inbox</CardLink> : undefined}>
                {!inboxLoaded ? (
                  <Skeleton rows={2} />
                ) : inbox.length === 0 ? (
                  <Quiet icon={CheckCircle2}>
                    <span className="font-medium text-fg">You're all caught up.</span> Approvals, questions and alerts from your agents show up here.
                  </Quiet>
                ) : (
                  <ul className="divide-y divide-line">
                    {inbox.slice(0, 4).map((item) => (
                      <li key={`${item.kind}:${item.id}`}>
                        <Link to={inboxHref(item)} className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-panel-2/60">
                          <span className={cn('w-[74px] flex-none rounded px-1.5 py-0.5 text-center font-mono text-[9.5px] tracking-[0.1em]', KIND_STYLE[item.kind].className)}>
                            {KIND_STYLE[item.kind].label}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[13.5px] font-medium group-hover:text-accent-ink">{inboxTitle(item)}</span>
                            <span className="block truncate text-[12px] text-fg-dim">{inboxSource(item)}</span>
                          </span>
                          {item.created_at && <span className="hidden flex-none font-mono text-[10.5px] text-fg-dim sm:block">{timeAgo(item.created_at, now)}</span>}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
                {inbox.length > 4 && (
                  <div className="border-t border-line px-4 py-2 font-mono text-[11px] text-fg-dim">+ {inbox.length - 4} more in your inbox</div>
                )}
              </Card>

              {/* Recent chats */}
              <Card title="Recent chats" icon={MessageSquare} action={<CardLink to="/home">All chats</CardLink>}>
                {chats.loading && !chats.data ? (
                  <Skeleton rows={4} />
                ) : chatList.length === 0 ? (
                  <Quiet icon={MessageSquare}>No chats yet. Ask something above to start one.</Quiet>
                ) : (
                  <ul className="divide-y divide-line">
                    {chatList.slice(0, 6).map((c) => {
                      const agent = c.agent_id ? agentById.get(c.agent_id) : undefined;
                      const name = c.kind === 'team' ? 'Team chat' : agent?.name || 'Agent';
                      const href = c.kind === 'team' && c.team_id ? `/teams/${c.team_id}/chat?chat=${encodeURIComponent(c.id)}` : `/home?chat=${encodeURIComponent(c.id)}`;
                      return (
                        <li key={c.id}>
                          <Link to={href} className="group flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-panel-2/60">
                            <AgentBadge name={name} team={c.kind === 'team'} />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[13.5px] font-medium group-hover:text-accent-ink">{c.title || 'New chat'}</span>
                              <span className="block truncate text-[12px] text-fg-dim">
                                {name} · {plural(c.message_count, 'message')}
                              </span>
                            </span>
                            {c.running ? (
                              <span className="flex flex-none items-center gap-1.5 rounded-full bg-accent/15 px-2 py-0.5 font-mono text-[10.5px] text-accent-ink">
                                <LiveDot /> working
                              </span>
                            ) : (
                              <span className="flex-none font-mono text-[10.5px] text-fg-dim">{timeAgo(c.updated_at, now)}</span>
                            )}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </Card>

              {/* Task runs */}
              <Card title="Task runs" icon={ListChecks} action={<CardLink to="/tasks">All tasks</CardLink>}>
                {tasks.loading && !tasks.data ? (
                  <Skeleton rows={3} />
                ) : recentTasks.length === 0 ? (
                  <Quiet icon={ListChecks}>
                    {taskList.length ? 'None of your tasks has run yet.' : 'No tasks yet.'}{' '}
                    <Link to="/tasks/new" className="text-accent-ink hover:underline">
                      Plan a task
                    </Link>{' '}
                    and agents work through it step by step.
                  </Quiet>
                ) : (
                  <ul className="divide-y divide-line">
                    {recentTasks.map((t) => {
                      const active = !!t.run_status && ACTIVE_RUN.has(t.run_status);
                      const failed = t.run_status === 'failed';
                      const cancelled = t.run_status === 'cancelled';
                      const when = active ? `started ${timeAgo(t.run_started_at, now)}` : timeAgo(t.run_completed_at || t.run_started_at, now);
                      return (
                        <li key={t.id}>
                          <Link to={`/tasks/${t.id}`} className="group flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-panel-2/60">
                            <span
                              className={cn(
                                'grid h-[22px] w-[22px] flex-none place-items-center rounded-full border',
                                active && 'border-accent/50 bg-accent/10',
                                failed && 'border-danger/40 bg-danger/10 text-danger-ink',
                                cancelled && 'border-line text-fg-dim',
                                !active && !failed && !cancelled && 'border-ok/40 bg-ok/10 text-ok',
                              )}
                              aria-hidden="true"
                            >
                              {active ? <LiveDot /> : failed ? '!' : cancelled ? <CircleDashed className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[13.5px] font-medium group-hover:text-accent-ink">{t.title}</span>
                              <span className={cn('block truncate font-mono text-[11px]', failed ? 'text-danger-ink' : active ? 'text-accent-ink' : 'text-fg-dim')}>
                                {t.run_status}
                              </span>
                            </span>
                            {when && <span className="flex-none font-mono text-[10.5px] text-fg-dim">{when}</span>}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </Card>
            </div>

            <div className="flex min-w-0 flex-col gap-4">
              {/* Agents */}
              <Card title="Your agents" icon={Bot} action={<CardLink to="/agents">All agents</CardLink>}>
                {agents.loading && !agents.data ? (
                  <Skeleton rows={3} />
                ) : topAgents.length === 0 ? (
                  <Quiet icon={Bot}>
                    No agents yet.{' '}
                    <Link to="/agents/new" className="text-accent-ink hover:underline">
                      Create one
                    </Link>
                    .
                  </Quiet>
                ) : (
                  <ul className="p-1.5">
                    {topAgents.map((a) => {
                      const model = a.llm?.model || a.llm?.provider || '';
                      const count = chatsPerAgent.get(a.id) ?? 0;
                      return (
                        <li key={a.id}>
                          <Link to={`/agents/${a.id}`} className="group flex items-center gap-3 rounded-[10px] px-2.5 py-2 transition-colors hover:bg-panel-2">
                            <AgentBadge name={a.name} size="lg" className="!h-8 !w-8 !text-[13px]" />
                            <span className="min-w-0 flex-1">
                              <span className="flex items-center gap-2">
                                <span className="truncate text-[13.5px] font-medium group-hover:text-accent-ink">{a.name}</span>
                                {workingAgents.has(a.id) && <LiveDot />}
                              </span>
                              {model && <span className="block truncate font-mono text-[10.5px] text-fg-dim">{model}</span>}
                            </span>
                            <span className="flex-none font-mono text-[10.5px] text-fg-dim">{plural(count, 'chat')}</span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </Card>

              {/* Coming up */}
              <Card title="Coming up" icon={Clock3} action={<CardLink to="/automations">Automations</CardLink>}>
                {automations.loading && !automations.data ? (
                  <Skeleton rows={2} />
                ) : upcoming.length === 0 ? (
                  <Quiet icon={Zap}>Give a task a schedule or a webhook and it runs on its own.</Quiet>
                ) : (
                  <ol className="relative px-4 py-3">
                    <span aria-hidden="true" className="absolute bottom-5 left-[23px] top-5 w-px bg-line" />
                    {upcoming.slice(0, 4).map(({ a, at }) => (
                      <li key={a.id}>
                        <Link to={`/tasks/${a.id}?automation=1`} className="group relative flex items-start gap-3 py-2">
                          <span
                            aria-hidden="true"
                            className={cn('relative mt-1 h-[9px] w-[9px] flex-none rounded-full border-2 bg-panel', at ? 'border-accent-ink' : 'border-line')}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[13px] font-medium group-hover:text-accent-ink">{a.title}</span>
                            <span className="block truncate font-mono text-[10.5px] text-fg-dim">{howItRuns(a)}</span>
                          </span>
                          <span className={cn('flex-none font-mono text-[10.5px]', at ? 'text-fg' : 'text-fg-dim')}>{at ? timeUntil(at, now) : '—'}</span>
                        </Link>
                      </li>
                    ))}
                  </ol>
                )}
              </Card>

              {/* Usage */}
              <Card title="Usage · 14 days" icon={Coins} action={<CardLink to="/settings/usage">Details</CardLink>}>
                {usage.loading && !usage.data ? (
                  <Skeleton rows={2} />
                ) : usage.error || !usage.data ? (
                  <Quiet icon={Coins}>Usage isn't available here.</Quiet>
                ) : (
                  <div className="p-4">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[22px] font-semibold tabular-nums tracking-tight">{fmt(amount(usage.data.total))}</span>
                      <span className="font-mono text-[10.5px] text-fg-dim">{formatTokens(tokensOf(usage.data.total))} tokens</span>
                    </div>
                    <ul className="mt-3 flex h-16 items-end gap-[3px]" aria-label="Usage per day">
                      {(() => {
                        const peak = Math.max(...days.map(amount), 0);
                        return days.map((d, i) => (
                          <li key={d.day} className="group flex h-full min-w-0 flex-1 items-end" title={`${d.day}: ${fmt(amount(d))}`}>
                            <span className="sr-only">
                              {d.day}: {fmt(amount(d))}
                            </span>
                            <span
                              aria-hidden="true"
                              className={cn('w-full rounded-[3px] transition-colors', i === days.length - 1 ? 'bg-accent' : 'bg-accent-ink/25 group-hover:bg-accent-ink/50 dark:bg-accent/45 dark:group-hover:bg-accent/80')}
                              style={{ height: peak > 0 && amount(d) > 0 ? `${Math.max(6, (amount(d) / peak) * 100)}%` : '3px' }}
                            />
                          </li>
                        ));
                      })()}
                    </ul>
                    {usage.data.by_model.length > 0 && (
                      <ul className="mt-4 space-y-2.5">
                        {usage.data.by_model.slice(0, 3).map((m) => {
                          const share = amount(usage.data!.total) > 0 ? amount(m) / amount(usage.data!.total) : 0;
                          return (
                            <li key={`${m.provider}:${m.model}`}>
                              <div className="flex items-center justify-between gap-2 text-[12px]">
                                <span className="truncate font-mono text-fg">{m.model}</span>
                                <span className="flex-none font-mono text-fg-dim">{Math.round(share * 100)}%</span>
                              </div>
                              <div className="mt-1 h-1 overflow-hidden rounded-full bg-panel-2">
                                <div className="h-full rounded-full bg-accent-ink/70" style={{ width: `${Math.max(2, share * 100)}%` }} />
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                )}
              </Card>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
