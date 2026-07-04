import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { usePolling } from '@/hooks/usePolling';
import { Button } from '@/lib/components/ui/button';
import { LoadingState, Spinner } from '@/components/list-ui';
import { TranscriptView } from '@/components/TranscriptView';
import {
  parseChatEntries,
  parseSessionDate,
  fmtDuration,
  fmtRelative,
  type BackendChatEntry,
} from '@/lib/transcript';
import { cn } from '@/lib/utils';

interface TaskData {
  id?: string;
  title?: string;
  description?: string;
  step_instructions?: Record<string, { step: string; done: boolean }>;
  status?: string;
}

interface RunSummary {
  id: string;
  datetime?: string;
  updated_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  message_count?: number;
}

const runTime = (r: RunSummary) =>
  parseSessionDate(r.started_at)?.getTime() ?? parseSessionDate(r.datetime)?.getTime() ?? 0;

const STATUS: Record<string, string> = {
  completed: 'border-ok/40 text-ok',
  in_progress: 'border-accent/40 text-accent-ink',
  failed: 'border-danger/40 text-danger-ink',
  pending: 'border-line text-fg-dim',
};

/** Task details: run history in a sidebar (latest first), the selected run's
 *  transcript on the right. The running run is polled live — the worker
 *  persists chat turn-by-turn, so growth arrives in step-sized chunks. */
export default function TaskDetail() {
  const { taskId } = useParams();
  const { data: task, loading: taskLoading, refetch } = useResource<TaskData>(taskId ? `/tasks/${taskId}` : null);
  const { data: runs, loading: runsLoading, error: runsError, refetch: refetchRuns } = useResource<RunSummary[]>(
    taskId ? `/sessions/tasks/${taskId}` : null,
  );

  const sorted = useMemo(() => [...(runs || [])].sort((a, b) => runTime(b) - runTime(a)), [runs]);
  const isRunning = task?.status === 'in_progress';
  const runningId = isRunning ? (sorted.find((r) => r.started_at && !r.completed_at)?.id ?? null) : null;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [chats, setChats] = useState<Record<string, BackendChatEntry[]>>({});
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadChat = async (id: string) => {
    try {
      const res = await api.get<BackendChatEntry[]>(`/sessions/${id}/chat`);
      setChats((p) => ({ ...p, [id]: res.data }));
    } catch {
      // pane shows its loading row; retried on next poll/select
    }
  };

  // Default to the latest run; jump to the live run when one starts.
  useEffect(() => {
    if (runningId) setSelectedId(runningId);
  }, [runningId]);
  useEffect(() => {
    if (!selectedId && sorted.length > 0) setSelectedId(sorted[0].id);
  }, [sorted, selectedId]);
  useEffect(() => {
    if (selectedId && !chats[selectedId]) void loadChat(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  // Live polling: task (status + step ticks) every tick; the running run's
  // transcript once discovered, the runs list until then. Back off after five
  // minutes — a dead worker leaves tasks in_progress forever.
  const pollStartRef = useRef<number | null>(null);
  const tickRef = useRef(0);
  useEffect(() => {
    pollStartRef.current = isRunning ? Date.now() : null;
    tickRef.current = 0;
  }, [isRunning]);
  usePolling(
    () => {
      tickRef.current += 1;
      const slow = pollStartRef.current !== null && Date.now() - pollStartRef.current > 300_000;
      if (slow && tickRef.current % 3 !== 0) return;
      void refetch({ silent: true });
      if (runningId) void loadChat(runningId);
      else void refetchRuns({ silent: true });
    },
    5000,
    !!isRunning,
  );

  // Final refresh when the run finishes: the last poll can predate the final
  // turns and the completed_at stamp.
  const prevStatusRef = useRef<string | undefined>(undefined);
  const lastRunningRef = useRef<string | null>(null);
  if (runningId) lastRunningRef.current = runningId;
  useEffect(() => {
    if (prevStatusRef.current === 'in_progress' && task?.status !== 'in_progress') {
      void refetchRuns({ silent: true });
      if (lastRunningRef.current) void loadChat(lastRunningRef.current);
    }
    prevStatusRef.current = task?.status;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.status]);

  // Keep the live transcript pinned to the bottom as it grows.
  const liveLen = selectedId && selectedId === runningId ? chats[selectedId]?.length ?? 0 : -1;
  useEffect(() => {
    if (liveLen < 0) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [liveLen]);

  // Placeholder while the worker hasn't picked the run up yet.
  const waitingSinceRef = useRef<number | null>(null);
  if (isRunning && !runningId) waitingSinceRef.current ??= Date.now();
  else waitingSinceRef.current = null;
  const waitingLong = waitingSinceRef.current !== null && Date.now() - waitingSinceRef.current > 30_000;

  const start = async () => {
    if (!taskId) return;
    setStarting(true);
    setError('');
    try {
      await api.get(`/tasks/start/${taskId}`);
      await refetch();
      await refetchRuns({ silent: true });
    } catch (e) {
      setError(errorMessage(e, 'Could not start the task.'));
    } finally {
      setStarting(false);
    }
  };

  const steps = Object.values(task?.step_instructions || {});
  const stepsDone = steps.filter((s) => s.done).length;
  const status = task?.status || 'pending';

  if (taskLoading && !task) {
    return <div className="flex-1 flex flex-col h-screen min-w-0 bg-bg"><LoadingState label="loading task…" /></div>;
  }

  return (
    <div className="flex-1 flex h-screen min-w-0 bg-bg text-fg">
      {/* Runs sidebar */}
      <aside className="hidden md:flex w-60 flex-none flex-col border-r border-line bg-panel">
        <div className="flex h-14 flex-none items-center justify-between border-b border-line px-4">
          <span className="font-mono text-[10px] tracking-[0.18em] text-fg-dim">RUNS · {sorted.length}</span>
          {runsError && (
            <button onClick={() => void refetchRuns()} className="font-mono text-[10.5px] text-danger-ink underline underline-offset-2">
              ↻ retry
            </button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto">
          {runsLoading && sorted.length === 0 ? (
            <div className="flex items-center gap-2 px-4 py-6 font-mono text-[11px] text-fg-dim"><Spinner className="h-3.5 w-3.5" /> loading…</div>
          ) : sorted.length === 0 ? (
            <p className="px-4 py-6 font-mono text-[11px] text-fg-dim">no runs yet — hit ▶ Run</p>
          ) : (
            sorted.map((r, idx) => {
              const live = r.id === runningId;
              const runStatus = live ? 'running' : r.completed_at ? 'completed' : 'interrupted';
              const started = parseSessionDate(r.started_at);
              return (
                <div
                  key={r.id}
                  onClick={() => setSelectedId(r.id)}
                  className={cn(
                    'cursor-pointer border-b border-line px-4 py-2.5 transition-colors',
                    r.id === selectedId ? 'bg-panel-2 border-l-2 border-l-accent' : 'hover:bg-panel-2',
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[12px] tnum">#{sorted.length - idx}</span>
                    {live ? (
                      <span className="flex items-center gap-1.5 font-mono text-[10.5px] text-accent-ink">
                        <span className="think-bars"><i /><i /><i /></span>
                        running
                      </span>
                    ) : (
                      <span className={cn('font-mono text-[10.5px]', runStatus === 'completed' ? 'text-ok' : 'text-fg-dim')}>
                        {runStatus}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 font-mono text-[10.5px] text-fg-dim">
                    {started ? fmtRelative(r.started_at) : 'unknown start'}
                    {!live && (fmtDuration(r.started_at, r.completed_at) ? ` · ${fmtDuration(r.started_at, r.completed_at)}` : '')}
                    {` · ${r.message_count ?? 0} msg`}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </aside>

      {/* Transcript pane */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 flex-none items-center gap-3 border-b border-line px-6">
          <Link
            to="/tasks"
            className="grid h-8 w-8 flex-none place-items-center rounded border border-line text-fg-dim transition-colors hover:border-fg-dim hover:text-fg"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
          </Link>
          <div className="min-w-0">
            <p className="font-mono text-[10px] tracking-[0.18em] text-accent-ink">TASK</p>
            <h1 className="truncate text-[15px] font-semibold tracking-tight">{task?.title || 'Task'}</h1>
          </div>
          <span className={cn('rounded border px-2 py-0.5 font-mono text-[10.5px]', STATUS[status] || STATUS.pending)}>{status}</span>
          {steps.length > 0 && (
            <span className="font-mono text-[10.5px] text-fg-dim tnum">steps {stepsDone}/{steps.length}</span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Button asChild variant="outline" size="sm">
              <Link to={`/tasks/${taskId}/edit`}>Edit</Link>
            </Button>
            <Button size="sm" onClick={start} disabled={starting || isRunning}>
              {starting ? 'Starting…' : isRunning ? 'Running…' : '▶ Run'}
            </Button>
          </div>
        </header>

        {error && (
          <p className="mx-6 mt-3 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>
        )}

        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {isRunning && !runningId && (
            <div className="mx-6 my-4 flex items-center gap-2.5 rounded border border-line bg-panel-2 px-3 py-2.5 font-mono text-[12px] text-fg-dim">
              <span className="think-bars"><i /><i /><i /><i /></span>
              waiting for a worker to pick up the run…
              {waitingLong && <span className="opacity-70">still waiting — check the Celery worker</span>}
            </div>
          )}
          {selectedId ? (
            chats[selectedId] ? (
              <TranscriptView entries={parseChatEntries(chats[selectedId])} live={selectedId === runningId} />
            ) : (
              <div className="flex items-center gap-2 px-6 py-4 font-mono text-[11px] text-fg-dim">
                <Spinner className="h-3.5 w-3.5" /> loading transcript…
              </div>
            )
          ) : (
            !isRunning && (
              <div className="mx-auto flex h-full max-w-2xl flex-col justify-center px-6">
                <p className="font-mono text-[12px] tracking-[0.04em] text-accent-ink">&gt;_ no runs yet</p>
                <h2 className="mt-3 text-2xl font-bold tracking-tight">Run this task</h2>
                <p className="mt-2 max-w-md text-fg-dim">
                  Hit ▶ Run to execute it — each run's transcript lands here, newest in the sidebar.
                </p>
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}
