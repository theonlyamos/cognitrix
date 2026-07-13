import { describe, expect, it } from 'vitest';
import {
  initialTaskRunLiveState,
  selectLiveTranscript,
  taskRunLiveReducer,
  type TaskRunEvent,
} from '@/lib/task-run-events';

const event = (
  sequence: number,
  kind: TaskRunEvent['kind'],
  data: Record<string, unknown>,
): TaskRunEvent => ({
  type: 'task_run_event',
  id: 'event-' + sequence,
  run_id: 'run-1',
  session_id: 'session-1',
  step_index: 0,
  sequence,
  kind,
  agent_name: 'Researcher',
  data,
});

describe('taskRunLiveReducer', () => {
  it('resets all run-scoped live state', () => {
    const state = taskRunLiveReducer({
      lastSequence: 9,
      sessions: {
        'session-1': [{
          kind: 'assistant',
          content: 'stale',
          live: true,
          turnId: 'session-1:1',
        }],
      },
      stepStatuses: { 0: 'done' },
      terminalStatus: 'completed',
    }, { type: 'reset' });

    expect(state).toEqual(initialTaskRunLiveState);
  });

  it('appends text and ignores replayed sequences', () => {
    const first = event(1, 'text_delta', {
      turn_id: 'session-1:1',
      attempt: 1,
      content: 'hello',
    });
    const second = event(2, 'text_delta', {
      turn_id: 'session-1:1',
      attempt: 1,
      content: ' world',
    });
    let state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: first,
    });
    state = taskRunLiveReducer(state, { type: 'event', event: second });
    state = taskRunLiveReducer(state, { type: 'event', event: second });

    expect(selectLiveTranscript(state, 'session-1')).toEqual([{
      kind: 'assistant',
      content: 'hello world',
      name: 'Researcher',
      live: true,
    }]);
    expect(state.lastSequence).toBe(2);
  });

  it('pairs tool completion with its running call', () => {
    let state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: event(1, 'tool_started', {
        turn_id: 'session-1:1',
        tool_call_id: 'call-1',
        tool_name: 'read_file',
        params: '{"path":"README.md"}',
      }),
    });
    state = taskRunLiveReducer(state, {
      type: 'event',
      event: event(2, 'tool_completed', {
        turn_id: 'session-1:1',
        tool_call_id: 'call-1',
        tool_name: 'read_file',
        result: 'contents',
        status: 'done',
      }),
    });

    const entries = selectLiveTranscript(state, 'session-1');
    expect(entries[0]).toMatchObject({
      kind: 'tool_calls',
      tools: [{
        id: 'call-1',
        name: 'read_file',
        status: 'done',
        result: 'contents',
      }],
    });
  });

  it('keeps an unmatched tool completion visible', () => {
    const state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: event(1, 'tool_completed', {
        turn_id: 'session-1:1',
        tool_call_id: 'call-missed-start',
        tool_name: 'read_file',
        result: 'late result',
        status: 'error',
      }),
    });

    expect(selectLiveTranscript(state, 'session-1')).toEqual([{
      kind: 'tool_calls',
      content: '',
      name: 'Researcher',
      tools: [{
        id: 'call-missed-start',
        name: 'read_file',
        args: '',
        result: 'late result',
        status: 'error',
      }],
    }]);
  });

  it('marks a completed turn as no longer live', () => {
    let state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: event(1, 'text_delta', {
        turn_id: 'session-1:1',
        content: 'finished response',
      }),
    });
    state = taskRunLiveReducer(state, {
      type: 'event',
      event: event(2, 'turn_completed', { turn_id: 'session-1:1' }),
    });

    expect(selectLiveTranscript(state, 'session-1')).toEqual([{
      kind: 'assistant',
      content: 'finished response',
      name: 'Researcher',
      live: false,
    }]);
  });

  it('tracks step and terminal run statuses', () => {
    let state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: event(1, 'step_status', { status: 'done' }),
    });
    state = taskRunLiveReducer(state, {
      type: 'event',
      event: { ...event(2, 'run_status', { status: 'completed' }), session_id: null },
    });

    expect(state.stepStatuses).toEqual({ 0: 'done' });
    expect(state.terminalStatus).toBe('completed');
  });

  it('pairs id-less same-name completions with running calls in FIFO order', () => {
    let state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: event(1, 'tool_started', {
        turn_id: 'session-1:1',
        tool_name: 'read_file',
        params: '{"path":"first.md"}',
      }),
    });
    state = taskRunLiveReducer(state, {
      type: 'event',
      event: event(2, 'tool_started', {
        turn_id: 'session-1:1',
        tool_name: 'read_file',
        params: '{"path":"second.md"}',
      }),
    });
    state = taskRunLiveReducer(state, {
      type: 'event',
      event: event(3, 'tool_completed', {
        turn_id: 'session-1:1',
        tool_name: 'read_file',
        result: 'first contents',
        status: 'done',
      }),
    });

    expect(selectLiveTranscript(state, 'session-1')[0]).toMatchObject({
      kind: 'tool_calls',
      tools: [
        {
          args: '{"path":"first.md"}',
          status: 'done',
          result: 'first contents',
        },
        {
          args: '{"path":"second.md"}',
          status: 'running',
        },
      ],
    });

    state = taskRunLiveReducer(state, {
      type: 'event',
      event: event(4, 'tool_completed', {
        turn_id: 'session-1:1',
        tool_name: 'read_file',
        result: 'second contents',
        status: 'done',
      }),
    });

    expect(selectLiveTranscript(state, 'session-1')[0]).toMatchObject({
      kind: 'tool_calls',
      tools: [
        {
          args: '{"path":"first.md"}',
          status: 'done',
          result: 'first contents',
        },
        {
          args: '{"path":"second.md"}',
          status: 'done',
          result: 'second contents',
        },
      ],
    });
  });

  it('keeps parallel sessions separate and reconciles one turn', () => {
    const session2 = {
      ...event(2, 'text_delta', {
        turn_id: 'session-2:1',
        attempt: 1,
        content: 'parallel',
      }),
      session_id: 'session-2',
      step_index: 1,
    };
    let state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: event(1, 'text_delta', {
        turn_id: 'session-1:1',
        attempt: 1,
        content: 'first',
      }),
    });
    state = taskRunLiveReducer(state, { type: 'event', event: session2 });
    state = taskRunLiveReducer(state, {
      type: 'reconcile',
      sessionId: 'session-1',
      turnId: 'session-1:1',
    });

    expect(selectLiveTranscript(state, 'session-1')).toEqual([]);
    expect(selectLiveTranscript(state, 'session-2')[0]).toMatchObject({
      content: 'parallel',
    });
  });
});
