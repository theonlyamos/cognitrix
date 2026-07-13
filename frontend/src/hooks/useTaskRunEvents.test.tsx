import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useTaskRunEvents } from '@/hooks/useTaskRunEvents';
import type { TaskRunEvent } from '@/lib/task-run-events';

interface StreamOptions {
  path: string | null;
  enabled: boolean;
  onEvent: (event: { data: TaskRunEvent }) => void;
}

const harness = vi.hoisted(() => ({
  useEventStream: vi.fn((options: StreamOptions) => {
    void options;
    return {
      isConnected: true,
      error: null,
      reconnect: vi.fn(),
    };
  }),
}));

vi.mock('@/hooks/useEventStream', () => ({
  useEventStream: harness.useEventStream,
}));

describe('useTaskRunEvents', () => {
  it('subscribes to the selected run endpoint', () => {
    const onEvent = vi.fn();
    renderHook(() => useTaskRunEvents({
      taskId: 'task-1',
      runId: 'run-1',
      onEvent,
    }));

    expect(harness.useEventStream).toHaveBeenCalledWith(expect.objectContaining({
      path: '/tasks/task-1/runs/run-1/events',
      enabled: true,
    }));
  });

  it('disables the stream until both task and run ids are available', () => {
    renderHook(() => useTaskRunEvents({
      taskId: 'task-1',
      runId: null,
      onEvent: vi.fn(),
    }));

    expect(harness.useEventStream).toHaveBeenCalledWith(expect.objectContaining({
      path: null,
      enabled: false,
    }));
  });

  it('URL-encodes task and run ids independently', () => {
    renderHook(() => useTaskRunEvents({
      taskId: 'task/one',
      runId: 'run two',
      onEvent: vi.fn(),
    }));

    expect(harness.useEventStream).toHaveBeenCalledWith(expect.objectContaining({
      path: '/tasks/task%2Fone/runs/run%20two/events',
      enabled: true,
    }));
  });

  it('forwards received event data to the callback', () => {
    const onEvent = vi.fn();
    renderHook(() => useTaskRunEvents({
      taskId: 'task-1',
      runId: 'run-1',
      onEvent,
    }));
    const calls = harness.useEventStream.mock.calls;
    const options = calls[calls.length - 1]?.[0];
    const received = {
      type: 'task_run_event' as const,
      id: 'event-1',
      run_id: 'run-1',
      session_id: 'session-1',
      step_index: 0,
      sequence: 1,
      kind: 'text_delta' as const,
      agent_name: 'Researcher',
      data: { turn_id: 'session-1:1', content: 'hello' },
    };

    options?.onEvent({ data: received });

    expect(onEvent).toHaveBeenCalledWith(received);
  });
});
