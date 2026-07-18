import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { usePolling } from '@/hooks/usePolling';
import { useTaskRunEvents } from '@/hooks/useTaskRunEvents';
import { Button } from '@/lib/components/ui/button';
import { LoadingState, Spinner } from '@/components/list-ui';
import { MobileSheet } from '@/components/MobileSheet';
import { MobileHeaderActions, type MobileHeaderAction } from '@/components/mobile-header-actions';
import { SelectionRow } from '@/components/SelectionRow';
import { ArtifactPreview } from '@/components/ArtifactPreview';
import { TranscriptView } from '@/components/TranscriptView';
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
}

// next_run_at is naive UTC — parse with an explicit Z, never as local time.
const nextRunLocal = (s: string) => new Date(s.replace(' ', 'T') + 'Z').toLocaleString();

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

const STATUS: Record<string, string> = {
  completed: 'border-ok/40 text-ok',
  in_progress: 'border-accent/40 text-accent-ink',
  failed: 'border-danger/40 text-danger-ink',
  cancelled: 'border-danger/30 text-fg-dim',
  pending: 'border-line text-fg-dim',
};

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
  worker_lost: 'Worker lost',
  lease_lost: 'Worker lease lost',
  queue_timeout: 'Queue timed out',
  budget_exceeded: 'Budget exceeded',
  cancelled: 'Cancelled',
  timeout: 'Run timed out',
  provider_error: 'Provider request failed',
  authority_invalid: 'Run authority invalid',
  capability_unavailable: 'Required capability unavailable',
  limit_backend_unavailable: 'Concurrency service unavailable',
  concurrency_exhausted: 'Concurrency capacity exhausted',
  validation_failed: 'Validation failed',
  persistence_error: 'Task state persistence failed',
  unknown: 'Run failed',
  queue_publish_failed: 'Queue publication failed',
  task_missing: 'Task unavailable',
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
    const usedLabel = (used ?? 0).toLocaleString();
    return [`${usedLabel}${limit === null ? '' : ` / ${limit.toLocaleString()}`} ${label}`];
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
        <div className="border-b border-line px-4 py-3 sm:pl-[116px]" aria-label="Result artifacts">
          <p className="mb-1 font-mono text-[10.5px] tracking-[0.08em] text-fg-dim">ARTIFACTS</p>
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
        <div className="border-b border-line px-4 py-3 sm:pl-[116px]" aria-label="Result sources">
          <p className="mb-1 font-mono text-[10.5px] tracking-[0.08em] text-fg-dim">SOURCES</p>
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
        <div className="border-b border-line px-4 py-3 sm:pl-[116px]" role="note" aria-label="Result warnings">
          <p className="mb-1 font-mono text-[10.5px] tracking-[0.08em] text-fg-dim">WARNINGS</p>
          <ul className="list-disc space-y-1 pl-4 font-mono text-[11px] text-fg-dim">
            {(result.warnings || []).map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        </div>
      )}
      {result.structured_data && (
        <details className="border-b border-line px-4 py-3 font-mono text-[11px] sm:pl-[116px]">
          <summary className="min-h-11 cursor-pointer text-fg-dim sm:min-h-0">Structured result</summary>
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
            toolCalls: response.data.tool_calls || [],
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
      setOlderRunsError(errorMessage(e, 'Could not load older runs.'));
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
      setError(errorMessage(e, 'Could not start the task.'));
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
      setError(errorMessage(e, 'Could not cancel the task.'));
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
      setError(errorMessage(e, 'Could not update the schedule.'));
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
  const cancelActionLabel = forceCancelReady ? 'Force cancel' : 'Cancelling\u2026';
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
  const selectedRunFailureLabel = selectedRun?.error_code
    ? FAILURE_LABEL[selectedRun.error_code]
      || (selectedRun.status === 'cancelled' ? 'Cancelled' : 'Run failed')
    : null;

  const mobileActions: MobileHeaderAction[] = [
    { key: 'edit', label: 'Edit task', to: `/tasks/${taskId}/edit` },
    ...(hasSchedule
      ? [{
          key: 'schedule',
          label: task?.schedule_enabled ? 'Pause task schedule' : 'Resume task schedule',
          disabled: togglingSchedule,
          onSelect: toggleSchedule,
        }]
      : []),
    ...(canResume ? [{ key: 'run-from-start', label: 'Run from beginning', disabled: starting, onSelect: () => start(false) }] : []),
  ];

  const runsPanel = (
    <>
      <div className="flex min-h-14 flex-none items-center justify-between gap-2 border-b border-line px-4">
        <span className="font-mono text-[10px] tracking-[0.18em] text-fg-dim">
          RUNS · {visibleRuns.length}{canLoadOlderRuns ? '+' : ''}
        </span>
        <div className="flex items-center gap-1">
          {runsError && (
            <div role="alert">
              <span className="sr-only">{runsError}</span>
              <Button variant="ghost" size="sm" onClick={() => void refetchRuns()} className="text-danger-ink">
                ↻ retry
              </Button>
            </div>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setMobileRunsOpen(false)}
            aria-label="Close runs"
            className="md:hidden"
          >
            <span aria-hidden>×</span>
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {runsLoading && !runs ? (
          <div role="status" className="flex items-center gap-2 px-4 py-6 font-mono text-[11px] text-fg-dim"><Spinner className="h-3.5 w-3.5" /> loading…</div>
        ) : visibleRuns.length === 0 && legacy.length === 0 ? (
          <p className="px-4 py-6 font-mono text-[11px] text-fg-dim">no runs yet — hit ▶ Run</p>
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
                      {r.status}
                    </span>
                  ) : (
                    <span className={cn('font-mono text-[10.5px]', RUN_BADGE[r.status] || 'text-fg-dim')}>{r.status}</span>
                  )}
                </div>
                <div className="mt-0.5 font-mono text-[10.5px] text-fg-dim">
                  {r.started_at ? fmtRelative(r.started_at) : '—'}
                  {!ACTIVE_RUN.has(r.status) && fmtDuration(r.started_at, r.completed_at) ? ` · ${fmtDuration(r.started_at, r.completed_at)}` : ''}
                  {` · ${r.plan.filter((s) => s.status === 'done').length}/${r.plan.length || '?'} steps`}
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
                    {olderRunsLoading ? 'Loading older runs...' : 'Load older runs'}
                  </Button>
                )}
              </div>
            )}
            {legacy.length > 0 && (
              <div className="border-t border-line">
                <div className="px-4 pb-1 pt-3 font-mono text-[10px] tracking-[0.14em] text-fg-dim">LEGACY RUNS</div>
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
                    {parseSessionDate(s.started_at)?.toLocaleString() || 'legacy run'} · {s.message_count ?? 0} msg
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
    return <div className="flex-1 flex flex-col h-screen min-w-0 bg-bg"><LoadingState label="loading task…" /></div>;
  }

  return (
    <div className="flex-1 flex h-screen min-w-0 bg-bg text-fg">
      <MobileSheet
        id="mobile-runs"
        label="Runs"
        open={mobileRunsOpen}
        onClose={() => setMobileRunsOpen(false)}
        triggerRef={mobileRunsTriggerRef}
      >
        {runsPanel}
      </MobileSheet>

      {/* Runs sidebar */}
      <aside className="hidden w-60 flex-none flex-col border-r border-line bg-panel md:flex">
        {runsPanel}
      </aside>

      {/* Main pane */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="app-page-header flex min-h-14 flex-none flex-row flex-nowrap items-center gap-2 border-b border-line py-2 pl-16 pr-4 md:flex-wrap md:px-6">
          <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
          <Link
            to="/tasks"
            aria-label="Back to tasks"
            className="grid h-11 w-11 flex-none place-items-center rounded border border-line text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:h-8 md:w-8"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
          </Link>
          <div className="min-w-0 overflow-hidden">
            <p className="sr-only font-mono text-[10px] tracking-[0.18em] text-accent-ink md:not-sr-only">TASK</p>
            <h1 className="truncate text-[15px] font-semibold tracking-tight">{task?.title || 'Task'}</h1>
          </div>
          </div>
          <span className={cn('sr-only rounded border px-2 py-0.5 font-mono text-[10.5px] md:not-sr-only', STATUS[status] || STATUS.pending)}>{status}</span>
          {plan.length > 0 && (
            <span className="sr-only font-mono text-[10.5px] text-fg-dim tnum md:not-sr-only">steps {stepsDone}/{plan.length}</span>
          )}
          {hasSchedule && (
            <span className="sr-only font-mono text-[10.5px] text-fg-dim md:not-sr-only" title="schedule">
              {task?.schedule_enabled && task?.next_run_at ? `⏱ next ${nextRunLocal(task.next_run_at)}` : '⏸ schedule paused'}
            </span>
          )}
          <div className="flex flex-none items-center gap-1 md:ml-auto md:gap-2">
            <Button
              ref={mobileRunsTriggerRef}
              variant="outline"
              size="icon"
              className="md:hidden"
              aria-label="Open runs"
              aria-controls="mobile-runs"
              aria-expanded={mobileRunsOpen}
              onClick={() => setMobileRunsOpen(true)}
            >
              <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 5h16v11H7l-3 3V5z" /></svg>
            </Button>
            {hasSchedule && (
              <Button variant="outline" size="sm" className="hidden md:inline-flex" onClick={toggleSchedule} disabled={togglingSchedule}>
                {task?.schedule_enabled ? 'Pause schedule' : 'Resume schedule'}
              </Button>
            )}
            <Button asChild variant="outline" size="sm" className="hidden md:inline-flex">
              <Link to={`/tasks/${taskId}/edit`}>Edit</Link>
            </Button>
            {isLive ? (
              <Button variant="ghost" size="sm" onClick={cancel} disabled={cancelActionDisabled} className="hidden hover:text-danger-ink md:inline-flex">
                {activeRun?.status === 'cancelling' || cancelling ? cancelActionLabel : 'Cancel'}
              </Button>
            ) : canResume ? (
              <div className="hidden md:contents">
                <Button variant="outline" size="sm" className="hidden md:inline-flex" onClick={() => start(true)} disabled={starting}>
                  {starting ? 'Starting…' : '↻ Resume'}
                </Button>
                <Button size="sm" onClick={() => start(false)} disabled={starting}>▶ Run</Button>
              </div>
            ) : (
              <Button size="sm" className="hidden md:inline-flex" onClick={() => start(false)} disabled={starting}>
                {starting ? 'Starting…' : '▶ Run'}
              </Button>
            )}
            {isLive ? (
              <Button
                variant="ghost"
                size="icon"
                className="md:hidden hover:text-danger-ink"
                aria-label={activeRun?.status === 'cancelling' || cancelling ? cancelActionLabel : 'Cancel task'}
                onClick={cancel}
                disabled={cancelActionDisabled}
              >
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
              </Button>
            ) : canResume ? (
              <Button variant="outline" size="icon" className="md:hidden" aria-label="Resume task" onClick={() => start(true)} disabled={starting}>
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 12a8 8 0 1 0 2-5.5" /><path d="M4 5v6h6" /></svg>
              </Button>
            ) : (
              <Button size="icon" className="md:hidden" aria-label={starting ? 'Starting task' : 'Run task'} onClick={() => start(false)} disabled={starting}>
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8 5l11 7-11 7V5z" /></svg>
              </Button>
            )}
            <MobileHeaderActions id="task-actions" actions={mobileActions} />
            {actionStatus && <span role="status" className="sr-only">{actionStatus}</span>}
          </div>
        </header>

        {error && (
          <p role="alert" className="mx-6 mt-3 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>
        )}

        {/* Steps panel for the selected run */}
        {selectedRun && plan.length > 0 && (
          <div className="flex flex-none flex-wrap gap-1.5 border-b border-line px-6 py-2.5">
            {plan.map((s) => (
              <button
                key={s.index}
                type="button"
                aria-pressed={selected === s.index}
                aria-label={`${s.title}${s.agent_name ? ` — ${s.agent_name}` : ''}${(s.attempts ?? 0) > 1 ? ` · ${s.attempts} attempts` : ''}${s.gate === 'unverified' ? ' · unverified' : ''}`}
                onClick={() => pickStep(s)}
                title={`${s.title}${s.agent_name ? ` — ${s.agent_name}` : ''}${(s.attempts ?? 0) > 1 ? ` · ${s.attempts} attempts` : ''}${s.gate === 'unverified' ? ' · unverified' : ''}`}
                className={cn(
                  'flex min-h-11 min-w-11 items-center gap-1.5 rounded border px-2 py-1 font-mono text-[11px] transition-colors md:min-h-0 md:min-w-0',
                  selected === s.index ? 'border-accent bg-panel-2 text-fg' : 'border-line text-fg-dim hover:border-fg-dim',
                  s.status === 'pending' && 'opacity-50 cursor-default',
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
                    unverified
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
                }}
                className={cn(
                  'flex min-h-11 items-center gap-1.5 rounded border px-2 py-1 font-mono text-[11px] transition-colors md:min-h-0',
                  selected === 'synthesis' ? 'border-accent bg-panel-2 text-fg' : 'border-line text-fg-dim hover:border-fg-dim',
                )}
              >
                Σ synthesis
              </button>
            )}
          </div>
        )}

        {(selectedRun?.error || selectedRunFailureLabel) && (
          <div role="alert" className="mx-6 mt-3 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">
            {selectedRunFailureLabel && <p className="font-semibold">{selectedRunFailureLabel}</p>}
            {selectedRun?.error && <p className="whitespace-pre-wrap">{selectedRun.error}</p>}
          </div>
        )}

        {selectedRunUsage.length > 0 && (
          <div className="mx-6 mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10.5px] text-fg-dim" aria-label="Run usage">
            {selectedRunUsage.map((item) => <span key={item}>{item}</span>)}
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
            <span>{runStreamConnected ? 'live progress' : 'live progress disconnected'}</span>
            {runStreamError && (
              <button
                type="button"
                onClick={reconnectRunStream}
                className="ml-auto min-h-11 underline underline-offset-2 md:min-h-0"
              >
                reconnect
              </button>
            )}
          </div>
        )}

        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {selectedRun?.status === 'queued' && (
            <div role="status" className="mx-6 my-4 flex items-center gap-2.5 rounded border border-line bg-panel-2 px-3 py-2.5 font-mono text-[12px] text-fg-dim">
              <span className="think-bars"><i /><i /><i /><i /></span>
              waiting for a worker to pick up the run…
            </div>
          )}
          {preferDurableResult && selectedDurableLoading ? (
            <div role="status" className="flex items-center gap-2 px-6 py-4 font-mono text-[11px] text-fg-dim">
              <Spinner className="h-3.5 w-3.5" /> loading durable result...
            </div>
          ) : preferDurableResult && selectedDurableResult ? (
            <DurableResultView
              result={selectedDurableResult}
              tools={selectedDurableToolCalls}
              agentName={selectedDurableTarget === 'synthesis' ? 'Final result' : selectedPlanStep?.agent_name}
              taskId={taskId}
              runId={selectedRunId || undefined}
            />
          ) : preferDurableResult && selectedDurableError && selectedDurableTarget !== null && selectedRunId ? (
            <div role="alert" className="px-6 py-4 font-mono text-[11px] text-danger-ink">
              <p>Could not load the durable result.</p>
              <button
                type="button"
                className="mt-2 min-h-11 underline underline-offset-2 md:min-h-0"
                onClick={() => void loadDurableResult(selectedRunId, selectedDurableTarget)}
              >
                retry result
              </button>
            </div>
          ) : preferDurableResult && selectedDurableLoaded ? (
            <div>
              <DurableToolCalls tools={selectedDurableToolCalls} agentName={selectedPlanStep?.agent_name} />
              <div className="px-6 py-4 font-mono text-[11px] text-fg-dim">this step has no persisted result</div>
            </div>
          ) : selectedSessionId ? (
            chats[selectedSessionId] || liveEntries.length > 0 ? (
              <TranscriptView
                entries={transcriptEntries}
                live={!!activeRun && selectedStepStatus === 'running'}
              />
            ) : (
              <div role="status" className="flex items-center gap-2 px-6 py-4 font-mono text-[11px] text-fg-dim">
                <Spinner className="h-3.5 w-3.5" /> loading transcript…
              </div>
            )
          ) : selectedDurableLoading ? (
            <div role="status" className="flex items-center gap-2 px-6 py-4 font-mono text-[11px] text-fg-dim">
              <Spinner className="h-3.5 w-3.5" /> loading durable result...
            </div>
          ) : selectedDurableResult ? (
            <DurableResultView
              result={selectedDurableResult}
              tools={selectedDurableToolCalls}
              agentName={selectedDurableTarget === 'synthesis' ? 'Final result' : selectedPlanStep?.agent_name}
              taskId={taskId}
              runId={selectedRunId || undefined}
            />
          ) : selectedDurableError && selectedDurableTarget !== null && selectedRunId ? (
            <div role="alert" className="px-6 py-4 font-mono text-[11px] text-danger-ink">
              <p>Could not load the durable result.</p>
              <button
                type="button"
                className="mt-2 min-h-11 underline underline-offset-2 md:min-h-0"
                onClick={() => void loadDurableResult(selectedRunId, selectedDurableTarget)}
              >
                retry result
              </button>
            </div>
          ) : selectedDurableLoaded ? (
            <div>
              <DurableToolCalls tools={selectedDurableToolCalls} agentName={selectedPlanStep?.agent_name} />
              <div className="px-6 py-4 font-mono text-[11px] text-fg-dim">this step has no persisted result</div>
            </div>
          ) : selectedRun ? (
            <div className="px-6 py-4 font-mono text-[11px] text-fg-dim">
              {plan.length === 0 ? 'this run failed before a plan was made' : 'select a step to view its transcript'}
            </div>
          ) : (
            !isTaskRunning && visibleRuns.length === 0 && (
              <div className="mx-auto flex h-full max-w-2xl flex-col justify-center px-6">
                <p className="font-mono text-[12px] tracking-[0.04em] text-accent-ink">&gt;_ no runs yet</p>
                <h2 className="mt-3 text-2xl font-bold tracking-tight">Run this task</h2>
                <p className="mt-2 max-w-md text-fg-dim">
                  Hit ▶ Run — the plan's steps land in the panel above, each with its own transcript, live while it executes.
                </p>
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}
