import type { TranscriptEntry, TranscriptTool } from '@/lib/transcript';

export type TaskRunEventKind =
  | 'step_status'
  | 'text_delta'
  | 'tool_started'
  | 'tool_completed'
  | 'turn_completed'
  | 'run_status';

export interface TaskRunEvent {
  type: 'task_run_event';
  id: string;
  run_id: string;
  session_id: string | null;
  step_index: number | null;
  sequence: number;
  kind: TaskRunEventKind;
  agent_name: string | null;
  data: Record<string, unknown>;
  created_at?: string | null;
}

type LiveEntry = TranscriptEntry & { turnId: string };

export interface TaskRunLiveState {
  lastSequence: number;
  sessions: Record<string, LiveEntry[]>;
  stepStatuses: Record<number, string>;
  terminalStatus: string | null;
}

export const initialTaskRunLiveState: TaskRunLiveState = {
  lastSequence: 0,
  sessions: {},
  stepStatuses: {},
  terminalStatus: null,
};

export type TaskRunLiveAction =
  | { type: 'event'; event: TaskRunEvent }
  | { type: 'reconcile'; sessionId: string; turnId: string }
  | { type: 'reset' };

const asString = (value: unknown): string =>
  typeof value === 'string' ? value : value == null ? '' : String(value);

export function taskRunLiveReducer(
  state: TaskRunLiveState,
  action: TaskRunLiveAction,
): TaskRunLiveState {
  if (action.type === 'reset') {
    return {
      lastSequence: 0,
      sessions: {},
      stepStatuses: {},
      terminalStatus: null,
    };
  }
  if (action.type === 'reconcile') {
    const entries = state.sessions[action.sessionId] || [];
    return {
      ...state,
      sessions: {
        ...state.sessions,
        [action.sessionId]: entries.filter(
          (entry) => entry.turnId !== action.turnId,
        ),
      },
    };
  }

  const event = action.event;
  if (event.sequence <= state.lastSequence) return state;
  const nextBase: TaskRunLiveState = {
    ...state,
    lastSequence: event.sequence,
  };

  if (event.kind === 'step_status' && event.step_index !== null) {
    return {
      ...nextBase,
      stepStatuses: {
        ...state.stepStatuses,
        [event.step_index]: asString(event.data.status),
      },
    };
  }
  if (event.kind === 'run_status') {
    return {
      ...nextBase,
      terminalStatus: asString(event.data.status) || null,
    };
  }
  if (!event.session_id) return nextBase;

  const sessionId = event.session_id;
  const entries = [...(state.sessions[sessionId] || [])];
  const turnId = asString(event.data.turn_id);
  const withEntries = (updated: LiveEntry[]): TaskRunLiveState => ({
    ...nextBase,
    sessions: { ...state.sessions, [sessionId]: updated },
  });

  if (event.kind === 'text_delta') {
    const content = asString(event.data.content);
    const lastIndex = entries.length - 1;
    const last = entries[lastIndex];
    if (
      last
      && last.kind === 'assistant'
      && last.turnId === turnId
      && last.live
    ) {
      entries[lastIndex] = { ...last, content: last.content + content };
    } else {
      entries.push({
        kind: 'assistant',
        content,
        name: event.agent_name || undefined,
        live: true,
        turnId,
      });
    }
    return withEntries(entries);
  }

  if (event.kind === 'tool_started') {
    const tool: TranscriptTool = {
      id: asString(event.data.tool_call_id) || undefined,
      name: asString(event.data.tool_name) || 'tool',
      args: asString(event.data.params),
      status: 'running',
    };
    const lastIndex = entries.length - 1;
    const last = entries[lastIndex];
    if (last && last.kind === 'tool_calls' && last.turnId === turnId) {
      entries[lastIndex] = { ...last, tools: [...last.tools, tool] };
    } else {
      entries.push({
        kind: 'tool_calls',
        content: '',
        name: event.agent_name || undefined,
        tools: [tool],
        turnId,
      });
    }
    return withEntries(entries);
  }

  if (event.kind === 'tool_completed') {
    const toolId = asString(event.data.tool_call_id);
    const toolName = asString(event.data.tool_name) || 'tool';
    let matched = false;
    const updated = entries.map((entry): LiveEntry => {
      if (entry.kind !== 'tool_calls' || entry.turnId !== turnId) return entry;
      const tools = entry.tools.map((tool) => {
        const same = toolId ? tool.id === toolId : tool.name === toolName;
        if (!same) return tool;
        matched = true;
        return {
          ...tool,
          status: asString(event.data.status) === 'error' ? 'error' : 'done',
          result: asString(event.data.result),
        } satisfies TranscriptTool;
      });
      return { ...entry, tools };
    });
    if (!matched) {
      updated.push({
        kind: 'tool_calls',
        content: '',
        name: event.agent_name || undefined,
        tools: [{
          id: toolId || undefined,
          name: toolName,
          args: '',
          status: asString(event.data.status) === 'error' ? 'error' : 'done',
          result: asString(event.data.result),
        }],
        turnId,
      });
    }
    return withEntries(updated);
  }

  if (event.kind === 'turn_completed') {
    return withEntries(entries.map((entry): LiveEntry => (
      entry.kind === 'assistant' && entry.turnId === turnId
        ? { ...entry, live: false }
        : entry
    )));
  }
  return nextBase;
}

export function selectLiveTranscript(
  state: TaskRunLiveState,
  sessionId: string | null,
): TranscriptEntry[] {
  if (!sessionId) return [];
  return (state.sessions[sessionId] || []).map((entry) => {
    const copy: Partial<LiveEntry> = { ...entry };
    delete copy.turnId;
    return copy as TranscriptEntry;
  });
}
