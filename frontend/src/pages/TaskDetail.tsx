import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { usePolling } from '@/hooks/usePolling';
import { useTaskRunEvents } from '@/hooks/useTaskRunEvents';
import { Button } from '@/lib/components/ui/button';
import { LoadingState, Spinner, StatusPill, Trail } from '@/components/list-ui';
import { money, statusLabel, tookWords } from '@/lib/plain';
import { MobileSheet } from '@/components/MobileSheet';
import { DesktopMoreMenu, MobileHeaderActions, type MobileHeaderAction } from '@/components/mobile-header-actions';
import { SelectionRow } from '@/components/SelectionRow';
import { ArtifactPreview } from '@/components/ArtifactPreview';
import { TranscriptView } from '@/components/TranscriptView';
import { parseCard } from '@/lib/cards';
import { TaskApprovals } from '@/components/TaskApprovals';
import { AutomationPanel, type TaskCreator } from '@/components/AutomationPanel';
import { TaskArtifacts } from '@/components/artifacts/TaskArtifacts';
import {
  parseChatEntries,
  parseSessionDate,
  fmtDuration,
  fmtRelative,
  type BackendChatEntry,
  type TranscriptEntry,
  type TranscriptTool,
} from '@/lib/transcript';
import { cn } from '@/lib/utils';
import {
  initialTaskRunLiveState,
  selectLiveTranscript,
  taskRunLiveReducer,
  type TaskRunEvent,
} from '@/lib/task-run-events';
import { tr, dateLocale } from '@/lib/i18n';

interface TaskData {
  id?: string;
  title?: string;
  description?: string;
  status?: string;
  schedule_at?: string | null;
  schedule_interval?: number | null;
  schedule_cron?: string | null;
  next_run_at?: string | null;
  schedule_enabled?: boolean;
  notes?: string | null;
  wake_pending?: boolean;
  wake_at?: string | null;
  wake_reason?: string | null;
  created_by?: TaskCreator | null;
  report_to?: string | null;
  report_only_on?: string | null;
  report_to_effective?: string;
  result_artifact?: string | null;
}

const TRIGGER_LABEL: Record<string, string> = {
  schedule: '⏱ schedule',
  wake: '↺ self-check',
  webhook: '⚡ webhook',
  email: '✉ new mail',
  github: '⑂ pull request news',
  agent: '◆ started by an agent',
  task_result: '↩ result of a task it made',
};

// next_run_at is naive UTC — parse with an explicit Z, never as local time.
const nextRunLocal = (s: string) => new Date(s.replace(' ', 'T') + 'Z').toLocaleString(dateLocale());

interface PlanStep {
  index: number;
  title: string;
  description?: string;
  agent_name?: string;
  dependencies?: number[];
  status: string;
  attempts?: number;
  gate?: string | null;
}

interface TaskRunRecord {
  id: string;
  status: string;
  force_cancel_ready?: boolean;
  plan: PlanStep[];
  result?: string | null;
  error?: string | null;
  error_code?: string | null;
  trigger_kind?: string | null;
  trigger_note?: string | null;
  usage?: Record<string, number | string | null> | null;
  budget?: Record<string, number | string | null> | null;
  queued_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at?: string;
}

interface ResultArtifact {
  id: string;
  name?: string | null;
  mime_type?: string | null;
  uri?: string | null;
}

interface ResultCitation {
  url: string;
  title?: string | null;
}

interface TypedTaskResult {
  text: string;
  artifacts?: ResultArtifact[];
  structured_data?: Record<string, unknown> | null;
  citations?: ResultCitation[];
  warnings?: string[];
  usage?: Record<string, number | string | null>;
}

interface DurableStepResult {
  step_index: number;
  status: string;
  result: TypedTaskResult | null;
  tool_calls?: TranscriptTool[];
}

interface DurableViewData {
  result: TypedTaskResult | null;
  toolCalls: TranscriptTool[];
}

/** A Show Card card from the server is checked again before it is drawn. */
const withCheckedCard = (tool: TranscriptTool): TranscriptTool => {
  const { card, ...rest } = tool;
  const checked = parseCard(card);
  return checked ? { ...rest, card: checked } : rest;
};

interface RunSession {
  id: string;
  step_index: number | null;
  step_title?: string | null;
}

interface LegacySession {
  id: string;
  run_id?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  message_count?: number;
}

const RUN_BADGE: Record<string, string> = {
  queued: 'text-accent-ink',
  running: 'text-accent-ink',
  cancelling: 'text-accent-ink',
  completed: 'text-ok',
  failed: 'text-danger-ink',
  cancelled: 'text-fg-dim',
};

const STEP_ICON: Record<string, string> = {
  pending: '·',
  running: '⋯',
  done: '✓',
  failed: '✕',
  skipped: '↷',
  cancelled: '⊘',
};

const ACTIVE_RUN = new Set(['queued', 'running', 'cancelling']);
const RUN_PAGE_SIZE = 50;

const FAILURE_LABEL: Record<string, string> = {
  get worker_lost() { return tr('ui.taskDetail.failure.workerLost'); },
  get lease_lost() { return tr('ui.taskDetail.failure.leaseLost'); },
  get queue_timeout() { return tr('ui.taskDetail.failure.queueTimeout'); },
  get budget_exceeded() { return tr('ui.taskDetail.failure.budgetExceeded'); },
  get cancelled() { return tr('ui.taskDetail.failure.cancelled'); },
  get timeout() { return tr('ui.taskDetail.failure.timeout'); },
  get provider_error() { return tr('ui.taskDetail.failure.providerError'); },
  get authority_invalid() { return tr('ui.taskDetail.failure.authorityInvalid'); },
  get capability_unavailable() { return tr('ui.taskDetail.failure.capabilityUnavailable'); },
  get limit_backend_unavailable() { return tr('ui.taskDetail.failure.limitBackendUnavailable'); },
  get concurrency_exhausted() { return tr('ui.taskDetail.failure.concurrencyExhausted'); },
  get validation_failed() { return tr('ui.taskDetail.failure.validationFailed'); },
  get persistence_error() { return tr('ui.taskDetail.failure.persistenceError'); },
  get unknown() { return tr('ui.taskDetail.failure.unknown'); },
  get queue_publish_failed() { return tr('ui.taskDetail.failure.queuePublishFailed'); },
  get task_missing() { return tr('ui.taskDetail.failure.taskMissing'); },
};

const numericValue = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
};

const safeResultHref = (value: string | null | undefined): string | null => {
  if (!value) return null;
  try {
    const parsed = new URL(value, window.location.origin);
    return ['http:', 'https:'].includes(parsed.protocol) ? value : null;
  } catch {
    return null;
  }
};

const taskArtifactPath = (
  taskId: string,
  runId: string,
  artifactId: string,
) => `/tasks/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`;

const isInternalResultArtifact = (
  value: string | null | undefined,
  taskId: string | undefined,
  runId: string | undefined,
  artifactId: string,
): value is string => !!(
  value
  && taskId
  && runId
  && value === taskArtifactPath(taskId, runId, artifactId)
);

/** "Took 2 min · about $0.04": what a finished run cost, in a line. */
const runSummary = (run: TaskRunRecord): string => {
  const parts: string[] = [];
  const started = parseSessionDate(run.started_at);
  const ended = parseSessionDate(run.completed_at);
  if (started && ended) parts.push(tr('ui.taskDetail.tookTime', { time: tookWords((ended.getTime() - started.getTime()) / 1000) }));
  const cost = money(run.usage?.cost_usd);
  if (cost) parts.push(tr('ui.taskDetail.aboutCost', { cost }));
  return parts.join(' · ');
};

const formatUsage = (run: TaskRunRecord): string[] => {
  const usage = run.usage || {};
  const budget = run.budget || {};
  const definitions = [
    ['total_tokens', 'max_tokens', 'tokens'],
    ['llm_calls', 'max_llm_calls', 'LLM calls'],
    ['tool_calls', 'max_tool_calls', 'tool calls'],
  ] as const;

  return definitions.flatMap(([usageKey, budgetKey, label]) => {
    const used = numericValue(usage[usageKey]);
    const limit = numericValue(budget[budgetKey]);
    if (used === null && limit === null) return [];
    const usedLabel = (used ?? 0).toLocaleString(dateLocale());
    return [`${usedLabel}${limit === null ? '' : ` / ${limit.toLocaleString(dateLocale())}`} ${label}`];
  });
};

function DurableResultView({
  result,
  tools,
  agentName,
  taskId,
  runId,
}: {
  result: TypedTaskResult;
  tools: TranscriptTool[];
  agentName?: string;
  taskId?: string;
  runId?: string;
}) {
  const entries: TranscriptEntry[] = result.text
    ? [{ kind: 'assistant', name: agentName, content: result.text }]
    : [];
  return (
    <div>
      <DurableToolCalls tools={tools} agentName={agentName} />
      {entries.length > 0 && <TranscriptView entries={entries} />}
      {(result.artifacts || []).length > 0 && (
        <div className="border-b border-line px-4 py-3 sm:pl-[116px]" aria-label={tr('ui.taskDetail.resultArtifacts')}>
          <p className="mb-1 font-mono text-[10.5px] tracking-[0.08em] text-fg-dim">{tr('ui.taskDetail.artifacts')}</p>
          <ul className="space-y-1 font-mono text-[11px]">
            {(result.artifacts || []).map((artifact) => (
              <li key={artifact.id}>
                {isInternalResultArtifact(
                  artifact.uri,
                  taskId,
                  runId,
                  artifact.id,
                ) && artifact.mime_type ? (
                  <ArtifactPreview
                    sourcePath={artifact.uri}
                    artifact={{
                      id: artifact.id,
                      mime_type: artifact.mime_type,
                      filename: artifact.name || undefined,
                    }}
                  />
                ) : safeResultHref(artifact.uri) ? (
                  <a href={safeResultHref(artifact.uri)!} target="_blank" rel="noreferrer nofollow" className="text-accent-ink underline underline-offset-2">
                    {artifact.name || artifact.id}
                  </a>
                ) : (
                  <span>{artifact.name || artifact.id}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(result.citations || []).length > 0 && (
        <div className="border-b border-line px-4 py-3 sm:pl-[116px]" aria-label={tr('ui.taskDetail.resultSources')}>
          <p className="mb-1 font-mono text-[10.5px] tracking-[0.08em] text-fg-dim">{tr('ui.taskDetail.sources')}</p>
          <ul className="space-y-1 font-mono text-[11px]">
            {(result.citations || []).map((citation) => (
              <li key={citation.url}>
                {safeResultHref(citation.url) ? (
                  <a href={safeResultHref(citation.url)!} target="_blank" rel="noreferrer nofollow" className="break-all text-accent-ink underline underline-offset-2">
                    {citation.title || citation.url}
                  </a>
                ) : (
                  <span className="break-all text-fg-dim">{citation.title || citation.url}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(result.warnings || []).length > 0 && (
        <div className="border-b border-line px-4 py-3 sm:pl-[116px]" role="note" aria-label={tr('ui.taskDetail.resultWarnings')}>
          <p className="mb-1 font-mono text-[10.5px] tracking-[0.08em] text-fg-dim">{tr('ui.taskDetail.warnings')}</p>
          <ul className="list-disc space-y-1 pl-4 font-mono text-[11px] text-fg-dim">
            {(result.warnings || []).map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        </div>
      )}
      {result.structured_data && (
        <details className="border-b border-line px-4 py-3 font-mono text-[11px] sm:pl-[116px]">
          <summary className="min-h-11 cursor-pointer text-fg-dim sm:min-h-0">{tr('ui.taskDetail.structuredResult')}</summary>
          <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-panel-2 p-2 text-fg-dim">
            {JSON.stringify(result.structured_data, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function DurableToolCalls({
  tools,
  agentName,
}: {
  tools: TranscriptTool[];
  agentName?: string;
}) {
  if (!tools.length) return null;
  return (
    <TranscriptView entries={[{
      kind: 'tool_calls',
      content: '',
      name: agentName,
      tools,
    }]} />
  );
}

/** Task details: TaskRun history in the sidebar, the selected run's steps
 *  panel + per-step transcript on the right. The active run is polled live
 *  (task status, plan step statuses, the watched step's transcript). */
export default function TaskDetail() {
  const { taskId } = useParams();
  const [searchParams] = useSearchParams();
  const [showAutomation, setShowAutomation] = useState(() => searchParams.get('automation') === '1');
  // Steps and the token counts are how the answer was reached: folded until asked for.
  const [stepsOpen, setStepsOpen] = useState<boolean | null>(null);
  const [usageOpen, setUsageOpen] = useState(false);
  const { data: task, loading: taskLoading, refetch } = useResource<TaskData>(taskId ? `/tasks/${taskId}` : null);
  const { data: runs, loading: runsLoading, error: runsError, refetch: refetchRuns } = useResource<TaskRunRecord[]>(
    taskId ? `/tasks/${taskId}/runs` : null,
  );
  // Legacy fallback: pre-redesign runs are bare sessions without a run_id.
  const { data: taskSessions } = useResource<LegacySession[]>(taskId ? `/sessions/tasks/${taskId}` : null);
  const legacy = useMemo(() => (taskSessions || []).filter((s) => !s.run_id), [taskSessions]);

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [olderRuns, setOlderRuns] = useState<TaskRunRecord[]>([]);
  const [olderRunsLoading, setOlderRunsLoading] = useState(false);
  const [olderRunsExhausted, setOlderRunsExhausted] = useState(false);
  const [olderRunsError, setOlderRunsError] = useState('');
  const firstRunPageSignatureRef = useRef<string | null>(null);
  // Selected transcript: a plan step index, the synthesis session, or a legacy session id.
  const [selected, setSelected] = useState<number | 'synthesis' | { legacy: string } | null>(null);
  const manualPickRef = useRef(false);
  const [stepSessions, setStepSessions] = useState<Record<string, Record<string, string>>>({});
  const [liveStepSessions, setLiveStepSessions] = useState<Record<string, Record<string, string>>>({});
  const [chats, setChats] = useState<Record<string, BackendChatEntry[]>>({});
  const [durableResults, setDurableResults] = useState<Record<string, DurableViewData>>({});
  const [durableResultLoading, setDurableResultLoading] = useState<Record<string, boolean>>({});
  const [durableResultErrors, setDurableResultErrors] = useState<Record<string, boolean>>({});
  const chatRequestGenerationRef = useRef(0);
  const chatRequestVersionsRef = useRef<Record<string, number>>({});
  const pendingCompletedTurnsRef = useRef<Record<string, Set<string>>>({});
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [togglingSchedule, setTogglingSchedule] = useState(false);
  const [mobileRunsOpen, setMobileRunsOpen] = useState(false);
  const mobileRunsTriggerRef = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const [liveState, dispatchLive] = useReducer(
    taskRunLiveReducer,
    initialTaskRunLiveState,
  );

  const visibleRuns = useMemo(() => {
    const seen = new Set<string>();
    return [...(runs || []), ...olderRuns].filter((run) => {
      if (seen.has(run.id)) return false;
      seen.add(run.id);
      return true;
    });
  }, [olderRuns, runs]);
  const selectedRun = useMemo(
    () => visibleRuns.find((r) => r.id === selectedRunId) || null,
    [selectedRunId, visibleRuns],
  );
  const newestRun = visibleRuns[0] || null;
  const activeRun = useMemo(
    () => visibleRuns.find((r) => ACTIVE_RUN.has(r.status)) || null,
    [visibleRuns],
  );
  const isTaskRunning = task?.status === 'in_progress';
  // A durable TaskRun is the source of truth. The task row is only a cached
  // projection and may lag after recovery or worker loss.
  const isLive = !!activeRun;

  const loadChat = useCallback(async (sessionId: string): Promise<boolean> => {
    const requestGeneration = chatRequestGenerationRef.current;
    const requestVersion = (chatRequestVersionsRef.current[sessionId] || 0) + 1;
    chatRequestVersionsRef.current[sessionId] = requestVersion;
    try {
      const res = await api.get<BackendChatEntry[]>(`/sessions/${sessionId}/chat`);
      if (
        chatRequestGenerationRef.current !== requestGeneration
        || chatRequestVersionsRef.current[sessionId] !== requestVersion
      ) {
        return false;
      }
      setChats((previous) => ({ ...previous, [sessionId]: res.data }));
      const pendingTurnIds = pendingCompletedTurnsRef.current[sessionId];
      if (pendingTurnIds) {
        delete pendingCompletedTurnsRef.current[sessionId];
        for (const turnId of pendingTurnIds) {
          dispatchLive({ type: 'reconcile', sessionId, turnId });
        }
      }
      return true;
    } catch {
      if (
        chatRequestGenerationRef.current === requestGeneration
        && chatRequestVersionsRef.current[sessionId] === requestVersion
      ) {
        // A canonical row can outlive its cleared or deleted transcript. Mark
        // an initial failure as settled so terminal views can fall back to the
        // durable result, but never erase a transcript loaded successfully by
        // an earlier refresh.
        setChats((previous) => (
          Object.prototype.hasOwnProperty.call(previous, sessionId)
            ? previous
            : { ...previous, [sessionId]: [] }
        ));
      }
      return false;
    }
  }, []);

  const loadRunSessions = useCallback(async (runId: string) => {
    try {
      const res = await api.get<RunSession[]>(`/sessions/runs/${runId}`);
      const map: Record<string, string> = {};
      for (const s of res.data) map[s.step_index === null ? 'synthesis' : String(s.step_index)] = s.id;
      setStepSessions((p) => ({ ...p, [runId]: map }));
      return map;
    } catch {
      return {};
    }
  }, []);

  const loadDurableResult = useCallback(async (
    runId: string,
    target: number | 'synthesis',
  ) => {
    if (!taskId) return;
    const key = `${runId}:${target}`;
    setDurableResultLoading((previous) => ({ ...previous, [key]: true }));
    setDurableResultErrors((previous) => ({ ...previous, [key]: false }));
    try {
      const path = target === 'synthesis'
        ? `/tasks/${taskId}/runs/${runId}/result`
        : `/tasks/${taskId}/runs/${runId}/steps/${target}/result`;
      if (target === 'synthesis') {
        const response = await api.get<TypedTaskResult>(path);
        setDurableResults((previous) => ({
          ...previous,
          [key]: { result: response.data, toolCalls: [] },
        }));
      } else {
        const response = await api.get<DurableStepResult>(path);
        setDurableResults((previous) => ({
          ...previous,
          [key]: {
            result: response.data.result,
            toolCalls: (response.data.tool_calls || []).map(withCheckedCard),
          },
        }));
      }
    } catch {
      setDurableResults((previous) => (
        Object.prototype.hasOwnProperty.call(previous, key)
          ? previous
          : { ...previous, [key]: { result: null, toolCalls: [] } }
      ));
      setDurableResultErrors((previous) => ({ ...previous, [key]: true }));
    } finally {
      setDurableResultLoading((previous) => ({ ...previous, [key]: false }));
    }
  }, [taskId]);

  useEffect(() => {
    const signature = `${taskId || ''}:${(runs || []).map((run) => run.id).join(',')}`;
    const previous = firstRunPageSignatureRef.current;
    firstRunPageSignatureRef.current = signature;
    if (previous !== null && previous !== signature) {
      // Offset pages are tied to the identity of page one. A newly inserted
      // run shifts every later offset, so discard older pages instead of
      // silently leaving a gap or duplicate in history.
      setOlderRuns([]);
      setOlderRunsExhausted(false);
      setOlderRunsError('');
    }
  }, [runs, taskId]);

  const loadOlderRuns = useCallback(async () => {
    if (!taskId || olderRunsLoading || olderRunsExhausted) return;
    setOlderRunsLoading(true);
    setOlderRunsError('');
    const offset = (runs || []).length + olderRuns.length;
    try {
      const response = await api.get<TaskRunRecord[]>(
        `/tasks/${taskId}/runs?limit=${RUN_PAGE_SIZE}&offset=${offset}`,
      );
      const page = response.data;
      setOlderRuns((previous) => {
        const existing = new Set(previous.map((run) => run.id));
        return [...previous, ...page.filter((run) => !existing.has(run.id))];
      });
      if (page.length < RUN_PAGE_SIZE) setOlderRunsExhausted(true);
    } catch (e) {
      setOlderRunsError(errorMessage(e, tr('ui.taskDetail.couldNotLoadOlderRuns')));
    } finally {
      setOlderRunsLoading(false);
    }
  }, [olderRuns.length, olderRunsExhausted, olderRunsLoading, runs, taskId]);

  const handleRunEvent = useCallback((event: TaskRunEvent) => {
    if (event.run_id !== selectedRunId) return;
    dispatchLive({ type: 'event', event });

    if (event.session_id && event.step_index !== null) {
      // Task executors intentionally keep protocol history ephemeral. Event
      // session ids identify live attempts, not canonical `/sessions` rows.
      setLiveStepSessions((previous) => ({
        ...previous,
        [event.run_id]: {
          ...(previous[event.run_id] || {}),
          [String(event.step_index)]: event.session_id!,
        },
      }));
    }

    if (event.kind === 'step_status') {
      void refetchRuns({ silent: true });
    }

    if (event.kind === 'turn_completed' && event.session_id) {
      const turnId = String(event.data.turn_id || '');
      const pendingTurnIds = pendingCompletedTurnsRef.current[event.session_id]
        || new Set<string>();
      pendingTurnIds.add(turnId);
      pendingCompletedTurnsRef.current[event.session_id] = pendingTurnIds;
      void loadChat(event.session_id);
    }

    if (event.kind === 'run_status') {
      void refetch({ silent: true });
      void refetchRuns({ silent: true });
      if (selectedRunId) void loadRunSessions(selectedRunId);
    }
  }, [loadChat, loadRunSessions, refetch, refetchRuns, selectedRunId]);

  const selectedRunIsLive = !!selectedRun && ACTIVE_RUN.has(selectedRun.status);
  const {
    isConnected: runStreamConnected,
    error: runStreamError,
    reconnect: reconnectRunStream,
  } = useTaskRunEvents({
    taskId,
    runId: selectedRunIsLive ? selectedRunId : null,
    onEvent: handleRunEvent,
  });

  useEffect(() => {
    chatRequestGenerationRef.current += 1;
    chatRequestVersionsRef.current = {};
    pendingCompletedTurnsRef.current = {};
    setChats({});
    dispatchLive({ type: 'reset' });
  }, [selectedRunId]);

  // Default run selection: the active run while live, else the newest.
  useEffect(() => {
    if (activeRun && selectedRunId !== activeRun.id) {
      setSelectedRunId(activeRun.id);
      manualPickRef.current = false;
      return;
    }
    if ((!selectedRunId || !selectedRun) && newestRun) {
      setSelectedRunId(newestRun.id);
      manualPickRef.current = false;
    }
  }, [activeRun, newestRun, selectedRun, selectedRunId]);

  // Load the step→session map when the selected run changes; pick a default step.
  useEffect(() => {
    if (!selectedRunId || !selectedRun) return;
    void loadRunSessions(selectedRunId);
    if (manualPickRef.current) return;
    const running = selectedRun.plan.find((s) => s.status === 'running');
    if (running) setSelected(running.index);
    else if (selectedRun.status === 'completed') setSelected('synthesis');
    else setSelected(selectedRun.plan.length ? selectedRun.plan[0].index : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId, selectedRun?.status]);

  // While live and un-piloted, follow the currently running step.
  useEffect(() => {
    if (!selectedRun || manualPickRef.current || !ACTIVE_RUN.has(selectedRun.status)) return;
    const running = selectedRun.plan.find((s) => s.status === 'running');
    if (running && selected !== running.index) setSelected(running.index);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRun]);

  // Resolve the selected transcript's session id and keep its chat loaded.
  const selectedSessionId = useMemo(() => {
    if (selected && typeof selected === 'object') return selected.legacy;
    if (!selectedRunId || selected === null) return null;
    const key = selected === 'synthesis' ? 'synthesis' : String(selected);
    const canonical = (stepSessions[selectedRunId] || {})[key];
    return canonical || (liveStepSessions[selectedRunId] || {})[key] || null;
  }, [liveStepSessions, selected, selectedRunId, stepSessions]);
  const selectedCanonicalSessionId = useMemo(() => {
    if (selected && typeof selected === 'object') return selected.legacy;
    if (!selectedRunId || selected === null) return null;
    const key = selected === 'synthesis' ? 'synthesis' : String(selected);
    return (stepSessions[selectedRunId] || {})[key] || null;
  }, [selected, selectedRunId, stepSessions]);

  const selectedDurableTarget = selectedRunId && (selected === 'synthesis' || typeof selected === 'number')
    ? selected
    : null;
  const selectedDurableKey = selectedRunId && selectedDurableTarget !== null
    ? `${selectedRunId}:${selectedDurableTarget}`
    : null;
  const selectedPlanStep = typeof selected === 'number'
    ? selectedRun?.plan.find((step) => step.index === selected)
    : null;
  const selectedStepStatus = typeof selected === 'number'
    ? liveState.stepStatuses[selected] || selectedPlanStep?.status
    : null;
  const durableResultEligible = selectedDurableTarget === 'synthesis'
    ? selectedRun?.status === 'completed'
    : !!selectedPlanStep && !!selectedStepStatus && !['pending', 'running'].includes(selectedStepStatus);
  const selectedDurableData = selectedDurableKey
    ? durableResults[selectedDurableKey]
    : undefined;
  const selectedDurableResult = selectedDurableData?.result;
  const selectedDurableToolCalls = selectedDurableData
    ? selectedDurableData.toolCalls
    : [];
  const selectedDurableLoaded = !!selectedDurableKey
    && Object.prototype.hasOwnProperty.call(durableResults, selectedDurableKey);
  const selectedDurableLoading = selectedDurableKey
    ? !!durableResultLoading[selectedDurableKey]
    : false;
  const selectedDurableError = selectedDurableKey
    ? !!durableResultErrors[selectedDurableKey]
    : false;
  const selectedChat = selectedSessionId ? chats[selectedSessionId] : undefined;
  const selectedCanonicalChat = selectedCanonicalSessionId
    ? chats[selectedCanonicalSessionId]
    : undefined;
  const selectedCanonicalEntries = selectedCanonicalChat
    ? parseChatEntries(selectedCanonicalChat)
    : [];
  const liveEntries = selectLiveTranscript(liveState, selectedSessionId);
  const canonicalChatSettled = !selectedCanonicalSessionId
    || selectedCanonicalChat !== undefined;
  const preferDurableResult = durableResultEligible
    && canonicalChatSettled
    && selectedCanonicalEntries.length === 0
    && (
      liveEntries.length === 0
      || selectedDurableLoaded
      || selectedDurableError
    );

  useEffect(() => {
    if (
      !selectedRunId
      || selectedDurableTarget === null
      || !selectedDurableKey
      || !durableResultEligible
      || selectedDurableLoading
      || Object.prototype.hasOwnProperty.call(durableResults, selectedDurableKey)
    ) return;
    void loadDurableResult(selectedRunId, selectedDurableTarget);
  }, [
    durableResultEligible,
    durableResults,
    loadDurableResult,
    selectedDurableKey,
    selectedDurableLoading,
    selectedDurableTarget,
    selectedRunId,
  ]);

  useEffect(() => {
    if (selectedSessionId && !selectedChat) void loadChat(selectedSessionId);
  }, [loadChat, selectedChat, selectedRunId, selectedSessionId]);

  // Live polling: task + runs (slim plans) + the map + the watched transcript.
  // Backs off after 5 minutes (a dead worker leaves runs active forever).
  const pollStartRef = useRef<number | null>(null);
  const tickRef = useRef(0);
  useEffect(() => {
    pollStartRef.current = isLive ? Date.now() : null;
    tickRef.current = 0;
  }, [isLive]);
  usePolling(
    () => {
      tickRef.current += 1;
      const slow = pollStartRef.current !== null && Date.now() - pollStartRef.current > 300_000;
      if (slow && tickRef.current % 3 !== 0) return;
      void refetch({ silent: true });
      void refetchRuns({ silent: true });
      if (selectedRunId) void loadRunSessions(selectedRunId);
      if (selectedSessionId) void loadChat(selectedSessionId);
    },
    5000,
    isLive,
  );

  // Final refresh when the run leaves the live state.
  const prevLiveRef = useRef(false);
  useEffect(() => {
    if (prevLiveRef.current && !isLive) {
      void refetchRuns({ silent: true });
      if (selectedRunId) void loadRunSessions(selectedRunId);
      if (selectedSessionId) void loadChat(selectedSessionId);
    }
    prevLiveRef.current = isLive;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLive]);

  // Pin the live transcript to the bottom as it grows.
  const liveRevision = selectedSessionId && activeRun
    ? (chats[selectedSessionId]?.length ?? 0) + liveState.lastSequence
    : -1;
  useEffect(() => {
    if (liveRevision < 0) return;
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [liveRevision]);

  const start = async (resume = false) => {
    if (!taskId) return;
    setStarting(true);
    setError('');
    try {
      await api.get(`/tasks/start/${taskId}${resume ? '?resume=true' : ''}`);
      manualPickRef.current = false;
      await refetch();
      await refetchRuns({ silent: true });
    } catch (e) {
      setError(errorMessage(e, tr('ui.taskDetail.couldNotStartTheTask')));
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!taskId) return;
    setCancelling(true);
    setError('');
    try {
      await api.post(`/tasks/${taskId}/cancel`);
      await refetch();
      await refetchRuns({ silent: true });
    } catch (e) {
      setError(errorMessage(e, tr('ui.taskDetail.couldNotCancelTheTask')));
    } finally {
      setCancelling(false);
    }
  };

  const hasSchedule = !!(task?.schedule_at || task?.schedule_interval || task?.schedule_cron);

  const toggleSchedule = async () => {
    if (!taskId) return;
    setTogglingSchedule(true);
    setError('');
    try {
      await api.post(`/tasks/${taskId}/schedule`, { enabled: !task?.schedule_enabled });
      // Idle-state polling is gated on isLive — refetch or the button goes stale.
      await refetch();
    } catch (e) {
      setError(errorMessage(e, tr('ui.taskDetail.couldNotUpdateTheSchedule')));
    } finally {
      setTogglingSchedule(false);
    }
  };

  const pickStep = (step: PlanStep) => {
    if (step.status === 'pending') return;
    manualPickRef.current = true;
    setSelected(step.index);
    // Click-miss on a just-started step: the map may predate its session.
    if (selectedRunId && !(stepSessions[selectedRunId] || {})[String(step.index)]) {
      void loadRunSessions(selectedRunId);
    }
  };

  const status = task?.status || 'pending';
  const plan = useMemo(
    () => (selectedRun?.plan || []).map((step) => ({
      ...step,
      status: liveState.stepStatuses[step.index] || step.status,
    })),
    [liveState.stepStatuses, selectedRun?.plan],
  );
  const stepsDone = plan.filter((s) => s.status === 'done').length;
  const canResume = !isLive && newestRun != null && (newestRun.status === 'failed' || newestRun.status === 'cancelled');
  const hasSynthesis = !!(
    selectedRunId
    && ((stepSessions[selectedRunId] || {})['synthesis'] || selectedRun?.status === 'completed')
  );
  const forceCancelReady = activeRun?.status === 'cancelling'
    && activeRun.force_cancel_ready === true;
  const cancelActionLabel = forceCancelReady ? tr('ui.taskDetail.forceCancel') : tr('ui.taskDetail.cancelling');
  const cancelActionDisabled = cancelling
    || (activeRun?.status === 'cancelling' && !forceCancelReady);
  const actionStatus = activeRun?.status === 'cancelling' || cancelling
    ? cancelActionLabel
    : starting
      ? 'Starting…'
      : null;
  const canLoadOlderRuns = !olderRunsExhausted
    && (runs || []).length === RUN_PAGE_SIZE;
  const canonicalEntries = selectedSessionId === selectedCanonicalSessionId
    ? selectedCanonicalEntries
    : selectedChat
      ? parseChatEntries(selectedChat)
      : [];
  const transcriptEntries = [...canonicalEntries, ...liveEntries];
  const selectedRunUsage = selectedRun ? formatUsage(selectedRun) : [];
  const selectedRunSummary = selectedRun ? runSummary(selectedRun) : '';
  const stepsShown = stepsOpen ?? (selectedRunIsLive || selectedRun?.status !== 'completed' || !hasSynthesis);
  // Older transcripts are opened only from the list, so it stays whenever there is one.
  const manyRuns = visibleRuns.length > 1 || legacy.length > 0;
  const selectedRunFailureLabel = selectedRun?.error_code
    ? FAILURE_LABEL[selectedRun.error_code]
      || (selectedRun.status === 'cancelled' ? tr('ui.taskDetail.failure.cancelled') : tr('ui.taskDetail.failure.unknown'))
    : null;

  const mobileActions: MobileHeaderAction[] = [
    { key: 'edit', label: tr('ui.taskDetail.editTask'), to: `/tasks/${taskId}/edit` },
    {
      key: 'automation',
      label: showAutomation ? tr('ui.taskDetail.hideNotesAndTriggers') : tr('ui.taskDetail.notesAndTriggers'),
      onSelect: () => setShowAutomation((v) => !v),
    },
    ...(hasSchedule
      ? [{
          key: 'schedule',
          label: task?.schedule_enabled ? tr('ui.taskDetail.pauseTaskSchedule') : tr('ui.taskDetail.resumeTaskSchedule'),
          disabled: togglingSchedule,
          onSelect: toggleSchedule,
        }]
      : []),
    ...(canResume ? [{ key: 'run-from-start', label: tr('ui.taskDetail.runFromBeginning'), disabled: starting, onSelect: () => start(false) }] : []),
  ];

  const runStepSelectors = (mobile = false) => (
    <div className={cn(
      'flex flex-wrap gap-1.5 border-b border-line px-4 py-2.5',
      mobile ? 'md:hidden' : 'hidden flex-none md:flex md:px-6',
    )}>
      {plan.map((s) => (
        <button
          key={s.index}
          type="button"
          aria-pressed={selected === s.index}
          aria-label={`${s.title}${s.agent_name ? ` — ${s.agent_name}` : ''}${(s.attempts ?? 0) > 1 ? ` · ${s.attempts} attempts` : ''}${s.gate === 'unverified' ? ' · unverified' : ''}`}
          onClick={() => {
            if (s.status === 'pending') return;
            pickStep(s);
            if (mobile) setMobileRunsOpen(false);
          }}
          title={`${s.title}${s.agent_name ? ` — ${s.agent_name}` : ''}${(s.attempts ?? 0) > 1 ? ` · ${s.attempts} attempts` : ''}${s.gate === 'unverified' ? ' · unverified' : ''}`}
          className={cn(
            'flex min-h-11 min-w-11 items-center gap-1.5 rounded border px-2 py-1 font-mono text-[11px] transition-colors md:min-h-0 md:min-w-0',
            selected === s.index ? 'border-accent bg-panel-2 text-fg' : 'border-line text-fg-dim hover:border-fg-dim',
            s.status === 'pending' && 'cursor-default opacity-50',
          )}
        >
          <span className={cn(
            s.status === 'done' && 'text-ok',
            s.status === 'failed' && 'text-danger-ink',
            s.status === 'running' && 'text-accent-ink',
          )}>{STEP_ICON[s.status] || '·'}</span>
          <span className="tnum">{s.index + 1}</span>
          {s.agent_name && <span className="max-w-32 truncate">{s.agent_name}</span>}
          {(s.attempts ?? 0) > 1 && <span className="text-fg-dim">×{s.attempts}</span>}
          {s.gate === 'unverified' && (
            <span className="rounded border border-line bg-panel px-1.5 py-0.5 text-[10px] text-fg">
              {tr('ui.taskDetail.unverified')}
            </span>
          )}
        </button>
      ))}
      {hasSynthesis && (
        <button
          type="button"
          aria-pressed={selected === 'synthesis'}
          onClick={() => {
            manualPickRef.current = true;
            setSelected('synthesis');
            if (mobile) setMobileRunsOpen(false);
          }}
          className={cn(
            'flex min-h-11 items-center gap-1.5 rounded border px-2 py-1 font-mono text-[11px] transition-colors md:min-h-0',
            selected === 'synthesis' ? 'border-accent bg-panel-2 text-fg' : 'border-line text-fg-dim hover:border-fg-dim',
          )}
        >
          {tr('ui.taskDetail.synthesis')}
        </button>
      )}
    </div>
  );

  const runsPanel = (mobile = false) => (
    <>
      <div className="flex min-h-14 flex-none items-center justify-between gap-2 border-b border-line px-4">
        <span className="font-mono text-[10px] tracking-[0.18em] text-fg-dim">
          {tr('ui.taskDetail.runsLengthValue', { length: visibleRuns.length, value: canLoadOlderRuns ? '+' : '' })}
        </span>
        <div className="flex items-center gap-1">
          {runsError && (
            <div role="alert">
              <span className="sr-only">{runsError}</span>
              <Button variant="ghost" size="sm" onClick={() => void refetchRuns()} className="text-danger-ink">
                {tr('ui.taskDetail.retry')}
              </Button>
            </div>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setMobileRunsOpen(false)}
            aria-label={tr('ui.taskDetail.closeRunDetails')}
            className="md:hidden"
          >
            <span aria-hidden>×</span>
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {mobile && selectedRun && (
          <div className="border-b border-line md:hidden">
            <div className="px-4 pb-1 pt-3 font-mono text-[10px] tracking-[0.14em] text-fg-dim">
              {tr('ui.taskDetail.selectedRunStatus', { status: selectedRun.status })}
            </div>
            {runStepSelectors(true)}
            {selectedRunUsage.length > 0 && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 px-4 py-3 font-mono text-[10.5px] text-fg-dim" aria-label={tr('ui.taskDetail.runUsage')}>
                {selectedRunUsage.map((item) => <span key={item}>{item}</span>)}
              </div>
            )}
          </div>
        )}
        {runsLoading && !runs ? (
          <div role="status" className="flex items-center gap-2 px-4 py-6 font-mono text-[11px] text-fg-dim"><Spinner className="h-3.5 w-3.5" />{' '}{tr('ui.taskDetail.loading')}</div>
        ) : visibleRuns.length === 0 && legacy.length === 0 ? (
          <p className="px-4 py-6 font-mono text-[11px] text-fg-dim">{tr('ui.taskDetail.noRunsYetHitRun')}</p>
        ) : (
          <>
            {visibleRuns.map((r, idx) => (
              <SelectionRow
                key={r.id}
                selected={r.id === selectedRunId}
                onSelect={() => {
                  manualPickRef.current = false;
                  setSelectedRunId(r.id);
                  setMobileRunsOpen(false);
                }}
                className={cn(
                  'min-h-11 border-b border-line transition-colors',
                  r.id === selectedRunId ? 'bg-panel-2 border-l-2 border-l-accent' : 'hover:bg-panel-2',
                )}
                buttonClassName="self-stretch px-4 py-2.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[12px] tnum">#{visibleRuns.length - idx}</span>
                  {ACTIVE_RUN.has(r.status) ? (
                    <span className="flex items-center gap-1.5 font-mono text-[10.5px] text-accent-ink">
                      <span className="think-bars"><i /><i /><i /></span>
                      {statusLabel(r.status)}
                    </span>
                  ) : (
                    <span className={cn('text-[11.5px]', RUN_BADGE[r.status] || 'text-fg-dim')}>{statusLabel(r.status)}</span>
                  )}
                </div>
                {r.trigger_kind && TRIGGER_LABEL[r.trigger_kind] && (
                  <div className="mt-0.5 truncate font-mono text-[10.5px] text-fg-dim" title={r.trigger_note || undefined}>
                    {TRIGGER_LABEL[r.trigger_kind]}
                  </div>
                )}
                <div className="mt-0.5 font-mono text-[10.5px] text-fg-dim">
                  {r.started_at ? fmtRelative(r.started_at) : '—'}
                  {!ACTIVE_RUN.has(r.status) && fmtDuration(r.started_at, r.completed_at) ? ` · ${fmtDuration(r.started_at, r.completed_at)}` : ''}
                  {tr('ui.taskDetail.lengthLength2Steps', { length: r.plan.filter((s) => s.status === 'done').length, length2: r.plan.length || '?' })}
                </div>
              </SelectionRow>
            ))}
            {(canLoadOlderRuns || olderRunsError) && (
              <div className="border-b border-line px-4 py-3">
                {olderRunsError && (
                  <p role="alert" className="mb-2 font-mono text-[10.5px] text-danger-ink">
                    {olderRunsError}
                  </p>
                )}
                {canLoadOlderRuns && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    onClick={() => void loadOlderRuns()}
                    disabled={olderRunsLoading}
                  >
                    {olderRunsLoading ? tr('ui.taskDetail.loadingOlderRuns') : tr('ui.taskDetail.loadOlderRuns')}
                  </Button>
                )}
              </div>
            )}
            {legacy.length > 0 && (
              <div className="border-t border-line">
                <div className="px-4 pb-1 pt-3 font-mono text-[10px] tracking-[0.14em] text-fg-dim">{tr('ui.taskDetail.legacyRuns')}</div>
                {legacy.map((s) => (
                  <SelectionRow
                    key={s.id}
                    selected={typeof selected === 'object' && selected?.legacy === s.id}
                    onSelect={() => {
                      manualPickRef.current = true;
                      setSelectedRunId(null);
                      setSelected({ legacy: s.id });
                      setMobileRunsOpen(false);
                    }}
                    className={cn(
                      'min-h-11 border-b border-line font-mono text-[10.5px] text-fg-dim transition-colors',
                      typeof selected === 'object' && selected?.legacy === s.id ? 'bg-panel-2 border-l-2 border-l-accent' : 'hover:bg-panel-2',
                    )}
                    buttonClassName="self-stretch px-4 py-2"
                  >
                    {tr('ui.taskDetail.parsesessiondateMessageCountMsg', { parseSessionDate: parseSessionDate(s.started_at)?.toLocaleString(dateLocale()) || tr('ui.taskDetail.legacyRun'), message_count: s.message_count ?? 0 })}
                  </SelectionRow>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </>
  );

  if (taskLoading && !task) {
    return <div className="flex-1 flex flex-col h-full min-w-0 bg-bg"><LoadingState label={tr('ui.taskDetail.loadingTask')} /></div>;
  }

  return (
    <div className="flex-1 flex h-full min-w-0 bg-bg text-fg">
      <MobileSheet
        id="mobile-run-details"
        label={tr('ui.taskDetail.runDetails')}
        open={mobileRunsOpen}
        onClose={() => setMobileRunsOpen(false)}
        triggerRef={mobileRunsTriggerRef}
      >
        {mobileRunsOpen ? runsPanel(true) : null}
      </MobileSheet>

      {/* Runs sidebar */}
      <aside className={cn('hidden w-60 flex-none flex-col border-r border-line bg-panel', manyRuns && 'md:flex')}>
        {runsPanel()}
      </aside>

      {/* Main pane */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="app-page-header flex min-h-14 flex-none flex-row flex-nowrap items-center gap-2 border-b border-line py-2 pl-16 pr-4 md:flex-wrap md:px-6">
          <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
          <Link
            to="/tasks"
            aria-label={tr('ui.taskDetail.backToTasks')}
            className="grid h-11 w-11 flex-none place-items-center rounded border border-line text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:h-8 md:w-8"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
          </Link>
          <div className="min-w-0 overflow-hidden">
            <div className="hidden md:block"><Trail steps={[{ label: tr('ui.taskDetail.tasks'), to: '/tasks' }]} /></div>
            <h1 className="text-[14px] font-semibold leading-tight tracking-tight max-md:line-clamp-2 md:truncate md:text-[15px]">{task?.title || tr('ui.taskDetail.task')}</h1>
          </div>
          </div>
          <span className="sr-only md:not-sr-only"><StatusPill status={isLive ? 'running' : status} /></span>
          {task?.wake_pending && task.wake_at && (
            <span className="sr-only font-mono text-[10.5px] text-fg-dim md:not-sr-only" title={task.wake_reason || tr('ui.taskDetail.nextCheck')}>
              {tr('ui.taskDetail.checkNextrunlocal', { nextRunLocal: nextRunLocal(task.wake_at) })}
            </span>
          )}
          {task?.created_by?.agent_id && (
            <span
              className="sr-only font-mono text-[10.5px] text-fg-dim md:not-sr-only"
              title={task.report_to_effective === 'person' ? tr('ui.taskDetail.resultsGoToYou') : tr('ui.taskDetail.resultsGoBackToThe')}
            >
              {tr('ui.taskDetail.madeByAgent', { agent: task.created_by.agent_name || tr('ui.taskDetail.anAgent') })}
            </span>
          )}
          {hasSchedule && (
            <span className="sr-only font-mono text-[10.5px] text-fg-dim md:not-sr-only" title={tr('ui.taskDetail.schedule')}>
              {task?.schedule_enabled && task?.next_run_at ? tr('ui.taskDetail.nextNextrunlocal', { nextRunLocal: nextRunLocal(task.next_run_at) }) : tr('ui.taskDetail.schedulePaused')}
            </span>
          )}
          <div className="flex flex-none items-center gap-1 md:ml-auto md:gap-2">
            {!selectedRun && (
              <Button
                ref={mobileRunsTriggerRef}
                variant="outline"
                size="icon"
                className="md:hidden"
                aria-label={tr('ui.taskDetail.openRuns')}
                aria-controls="mobile-run-details"
                aria-expanded={mobileRunsOpen}
                onClick={() => setMobileRunsOpen(true)}
              >
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 5h16v11H7l-3 3V5z" /></svg>
              </Button>
            )}
            <Button asChild variant="outline" size="sm" className="hidden md:inline-flex">
              <Link to={`/tasks/${taskId}/edit`}>{tr('ui.taskDetail.edit')}</Link>
            </Button>
            {isLive ? (
              <Button variant="outline" size="sm" onClick={cancel} disabled={cancelActionDisabled} className="hidden text-danger-ink md:inline-flex">
                {activeRun?.status === 'cancelling' || cancelling ? cancelActionLabel : tr('ui.taskDetail.stopRun')}
              </Button>
            ) : canResume ? (
              <div className="hidden md:contents">
                <Button variant="outline" size="sm" className="hidden md:inline-flex" onClick={() => start(true)} disabled={starting}>
                  {starting ? tr('ui.taskDetail.starting') : tr('ui.taskDetail.resume')}
                </Button>
                <Button size="sm" onClick={() => start(false)} disabled={starting}>{tr('ui.taskDetail.run')}</Button>
              </div>
            ) : (
              <Button size="sm" className="hidden md:inline-flex" onClick={() => start(false)} disabled={starting}>
                {starting ? tr('ui.taskDetail.starting') : tr('ui.taskDetail.run')}
              </Button>
            )}
            {isLive ? (
              <Button
                variant="ghost"
                size="icon"
                className="md:hidden hover:text-danger-ink"
                aria-label={activeRun?.status === 'cancelling' || cancelling ? cancelActionLabel : tr('ui.taskDetail.stopRun')}
                onClick={cancel}
                disabled={cancelActionDisabled}
              >
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
              </Button>
            ) : canResume ? (
              <Button variant="outline" size="icon" className="md:hidden" aria-label={tr('ui.taskDetail.resumeTask')} onClick={() => start(true)} disabled={starting}>
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 12a8 8 0 1 0 2-5.5" /><path d="M4 5v6h6" /></svg>
              </Button>
            ) : (
              <Button size="icon" className="md:hidden" aria-label={starting ? tr('ui.taskDetail.startingTask') : tr('ui.taskDetail.runTask')} onClick={() => start(false)} disabled={starting}>
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8 5l11 7-11 7V5z" /></svg>
              </Button>
            )}
            <DesktopMoreMenu actions={mobileActions.filter((action) => action.key === 'schedule')} />
            <MobileHeaderActions id="task-actions" actions={mobileActions} />
            {actionStatus && <span role="status" className="sr-only">{actionStatus}</span>}
          </div>
        </header>

        {error && (
          <p role="alert" className="mx-6 mt-3 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>
        )}

        <div role="tablist" aria-label={tr('ui.taskDetail.taskViews')} className="flex flex-none gap-5 border-b border-line px-4 md:px-6">
          {([[false, tr('ui.taskDetail.resultTab')], [true, tr('ui.taskDetail.settingsTab')]] as const).map(([on, label]) => (
            <button
              key={String(on)}
              type="button"
              role="tab"
              aria-selected={showAutomation === on}
              onClick={() => setShowAutomation(on)}
              className={cn(
                '-mb-px min-h-11 border-b-2 text-[13.5px] transition-colors md:min-h-0 md:py-2',
                showAutomation === on ? 'border-accent font-medium text-fg' : 'border-transparent text-fg-dim hover:text-fg',
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {showAutomation && taskId && (
          <div className="flex-1 overflow-y-auto">
            <AutomationPanel taskId={taskId} task={task} onChanged={() => refetch({ silent: true })} />
          </div>
        )}

        <div className={cn('min-h-0 flex-1 flex-col', showAutomation ? 'hidden' : 'flex')}>
        {taskId && <TaskArtifacts taskId={taskId} refreshKey={`${task?.status ?? ""}:${(runs || []).map((r) => `${r.id}:${r.status}`).join(",")}`} />}

        {selectedRun && (
          <button
            ref={mobileRunsTriggerRef}
            type="button"
            className="flex min-h-11 w-full flex-none items-center gap-2 border-b border-line px-4 text-left font-mono text-[10.5px] text-fg-dim transition-colors hover:bg-panel-2 md:hidden"
            aria-label={tr('ui.taskDetail.openRunDetailsStatusStepsdone', { status: selectedRun.status, stepsDone, length: plan.length })}
            aria-controls="mobile-run-details"
            aria-expanded={mobileRunsOpen}
            onClick={() => setMobileRunsOpen(true)}
          >
            <span className={cn('flex-none', RUN_BADGE[selectedRun.status] || 'text-fg-dim')}>
              {statusLabel(selectedRun.status)}
            </span>
            <span className="flex-none tnum">{tr('ui.taskDetail.stepsdoneLengthSteps', { stepsDone, length: plan.length })}</span>
            <span aria-hidden className="ml-auto flex-none text-fg">›</span>
          </button>
        )}

        {/* Steps panel for the selected run */}
        {selectedRun && plan.length > 0 && (
          <>
            <button
              type="button"
              aria-expanded={stepsShown}
              onClick={() => setStepsOpen(!stepsShown)}
              className="hidden flex-none items-center gap-2 border-b border-line px-6 py-2 text-left text-[13px] text-fg-dim transition-colors hover:text-fg md:flex"
            >
              <span aria-hidden className={cn('transition-transform', stepsShown && 'rotate-90')}>▸</span>
              {selectedRunIsLive ? tr('ui.taskDetail.howItIsGoing') : tr('ui.taskDetail.howItGotThere')}
              <span className="tnum">{tr('ui.taskDetail.stepsOf', { stepsDone, length: plan.length })}</span>
            </button>
            {stepsShown && runStepSelectors()}
          </>
        )}

        {(selectedRun?.error || selectedRunFailureLabel) && (
          <div role="alert" className="mx-6 mt-3 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">
            {selectedRunFailureLabel && <p className="font-semibold">{selectedRunFailureLabel}</p>}
            {selectedRun?.error && <p className="whitespace-pre-wrap">{selectedRun.error}</p>}
          </div>
        )}

        {taskId && selectedRun && selectedRunIsLive && (
          <TaskApprovals taskId={taskId} runId={selectedRun.id} live={selectedRunIsLive} />
        )}

        {selectedRun && (selectedRunSummary || selectedRunUsage.length > 0) && (
          <div className="mx-6 mt-2 hidden flex-wrap items-baseline gap-x-3 gap-y-1 text-[12.5px] text-fg-dim md:flex" aria-label={tr('ui.taskDetail.runUsage')}>
            {selectedRunSummary && <span>{selectedRunSummary}</span>}
            {selectedRunUsage.length > 0 && (
              <button type="button" aria-expanded={usageOpen} onClick={() => setUsageOpen((v) => !v)} className="underline-offset-2 hover:text-fg hover:underline">
                {usageOpen ? tr('ui.taskDetail.hideDetails') : tr('ui.taskDetail.details')}
              </button>
            )}
            {usageOpen && selectedRunUsage.map((item) => <span key={item} className="tnum">{item}</span>)}
          </div>
        )}

        {selectedRunIsLive && (
          <div className="flex flex-none items-center gap-2 border-b border-line px-6 py-1.5 font-mono text-[10.5px] text-fg-dim">
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                runStreamConnected ? 'bg-accent' : 'bg-danger',
              )}
            />
            <span>{runStreamConnected ? tr('ui.taskDetail.liveProgress') : tr('ui.taskDetail.liveProgressDisconnected')}</span>
            {runStreamError && (
              <button
                type="button"
                onClick={reconnectRunStream}
                className="ml-auto min-h-11 underline underline-offset-2 md:min-h-0"
              >
                {tr('ui.taskDetail.reconnect')}
              </button>
            )}
          </div>
        )}

        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {selectedRun?.status === 'queued' && (
            <div role="status" className="mx-6 my-4 flex items-center gap-2.5 rounded border border-line bg-panel-2 px-3 py-2.5 font-mono text-[12px] text-fg-dim">
              <span className="think-bars"><i /><i /><i /><i /></span>
              {tr('ui.taskDetail.waitingForAWorkerTo')}
            </div>
          )}
          {preferDurableResult && selectedDurableLoading ? (
            <div role="status" className="flex items-center gap-2 px-6 py-4 font-mono text-[11px] text-fg-dim">
              <Spinner className="h-3.5 w-3.5" />{' '}{tr('ui.taskDetail.loadingDurableResult')}
            </div>
          ) : preferDurableResult && selectedDurableResult ? (
            <DurableResultView
              result={selectedDurableResult}
              tools={selectedDurableToolCalls}
              agentName={selectedDurableTarget === 'synthesis' ? tr('ui.taskDetail.finalResult') : selectedPlanStep?.agent_name}
              taskId={taskId}
              runId={selectedRunId || undefined}
            />
          ) : preferDurableResult && selectedDurableError && selectedDurableTarget !== null && selectedRunId ? (
            <div role="alert" className="px-6 py-4 font-mono text-[11px] text-danger-ink">
              <p>{tr('ui.taskDetail.couldNotLoadTheDurable')}</p>
              <button
                type="button"
                className="mt-2 min-h-11 underline underline-offset-2 md:min-h-0"
                onClick={() => void loadDurableResult(selectedRunId, selectedDurableTarget)}
              >
                {tr('ui.taskDetail.retryResult')}
              </button>
            </div>
          ) : preferDurableResult && selectedDurableLoaded ? (
            <div>
              <DurableToolCalls tools={selectedDurableToolCalls} agentName={selectedPlanStep?.agent_name} />
              <div className="px-6 py-4 font-mono text-[11px] text-fg-dim">{tr('ui.taskDetail.thisStepHasNoPersisted')}</div>
            </div>
          ) : selectedSessionId ? (
            chats[selectedSessionId] || liveEntries.length > 0 ? (
              <TranscriptView
                entries={transcriptEntries}
                live={!!activeRun && selectedStepStatus === 'running'}
              />
            ) : (
              <div role="status" className="flex items-center gap-2 px-6 py-4 font-mono text-[11px] text-fg-dim">
                <Spinner className="h-3.5 w-3.5" />{' '}{tr('ui.taskDetail.loadingTranscript')}
              </div>
            )
          ) : selectedDurableLoading ? (
            <div role="status" className="flex items-center gap-2 px-6 py-4 font-mono text-[11px] text-fg-dim">
              <Spinner className="h-3.5 w-3.5" />{' '}{tr('ui.taskDetail.loadingDurableResult')}
            </div>
          ) : selectedDurableResult ? (
            <DurableResultView
              result={selectedDurableResult}
              tools={selectedDurableToolCalls}
              agentName={selectedDurableTarget === 'synthesis' ? tr('ui.taskDetail.finalResult') : selectedPlanStep?.agent_name}
              taskId={taskId}
              runId={selectedRunId || undefined}
            />
          ) : selectedDurableError && selectedDurableTarget !== null && selectedRunId ? (
            <div role="alert" className="px-6 py-4 font-mono text-[11px] text-danger-ink">
              <p>{tr('ui.taskDetail.couldNotLoadTheDurable')}</p>
              <button
                type="button"
                className="mt-2 min-h-11 underline underline-offset-2 md:min-h-0"
                onClick={() => void loadDurableResult(selectedRunId, selectedDurableTarget)}
              >
                {tr('ui.taskDetail.retryResult')}
              </button>
            </div>
          ) : selectedDurableLoaded ? (
            <div>
              <DurableToolCalls tools={selectedDurableToolCalls} agentName={selectedPlanStep?.agent_name} />
              <div className="px-6 py-4 font-mono text-[11px] text-fg-dim">{tr('ui.taskDetail.thisStepHasNoPersisted')}</div>
            </div>
          ) : selectedRun ? (
            <div className="px-6 py-4 font-mono text-[11px] text-fg-dim">
              {plan.length === 0 ? tr('ui.taskDetail.thisRunFailedBeforeA') : tr('ui.taskDetail.selectAStepToView')}
            </div>
          ) : (
            !isTaskRunning && visibleRuns.length === 0 && (
              <div className="mx-auto flex h-full max-w-2xl flex-col justify-center px-6">
                <p className="font-mono text-[12px] tracking-[0.04em] text-accent-ink">{tr('ui.taskDetail.noRunsYet')}</p>
                <h2 className="mt-3 text-2xl font-bold tracking-tight">{tr('ui.taskDetail.runThisTask')}</h2>
                <p className="mt-2 max-w-md text-fg-dim">
                  {tr('ui.taskDetail.hitRunThePlanS')}
                </p>
              </div>
            )
          )}
        </div>
        </div>
      </div>
    </div>
  );
}
