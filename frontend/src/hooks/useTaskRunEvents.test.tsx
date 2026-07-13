import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useTaskRunEvents } from '@/hooks/useTaskRunEvents';

const harness = vi.hoisted(() => ({
  useEventStream: vi.fn(() => ({
    isConnected: true,
    error: null,
    reconnect: vi.fn(),
  })),
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
});
