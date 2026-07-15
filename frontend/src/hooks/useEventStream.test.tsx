import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useEventStream } from '@/hooks/useEventStream';

describe('useEventStream', () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('sends the replay cursor and delivers parsed JSON frames', async () => {
    const encoder = new TextEncoder();
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({
          done: false,
          value: encoder.encode('id: 8\nevent: task_run\ndata: {"value":"ok"}\n\n'),
        })
        .mockResolvedValueOnce({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => reader },
    });
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('token', 'test-token');
    const onEvent = vi.fn();

    const { unmount } = renderHook(() => useEventStream({
      path: '/tasks/task-1/runs/run-1/events',
      onEvent,
      initialLastEventId: '7',
      autoReconnect: false,
    }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledWith({
      id: '8', event: 'task_run', data: { value: 'ok' },
    }));
    expect(fetchMock.mock.calls[0][1].headers).toMatchObject({
      Authorization: 'Bearer test-token',
      'Last-Event-ID': '7',
    });
    unmount();
    expect(reader.cancel).toHaveBeenCalledOnce();
  });

  it('reconnects from the last delivered event id', async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    const firstReader = {
      read: vi.fn()
        .mockResolvedValueOnce({
          done: false,
          value: encoder.encode('id: 8\ndata: {"value":"first"}\n\n'),
        })
        .mockRejectedValueOnce(new Error('stream dropped')),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const secondReader = {
      read: vi.fn(() => new Promise(() => undefined)),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => firstReader } })
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => secondReader } });
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('token', 'test-token');
    const onEvent = vi.fn();

    const { unmount } = renderHook(() => useEventStream({
      path: '/events', onEvent, maxRetries: 1,
    }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onEvent).toHaveBeenCalledOnce();

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].headers).toMatchObject({
      'Last-Event-ID': '8',
    });
    unmount();
  });

  it('does not advance the replay cursor when onEvent throws', async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    const firstReader = {
      read: vi.fn().mockResolvedValueOnce({
        done: false,
        value: encoder.encode('id: 8\ndata: {"value":"first"}\n\n'),
      }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const secondReader = {
      read: vi.fn(() => new Promise(() => undefined)),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => firstReader } })
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => secondReader } });
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('token', 'test-token');

    const { unmount } = renderHook(() => useEventStream({
      path: '/events',
      initialLastEventId: '7',
      onEvent: () => { throw new Error('consumer failed'); },
      maxRetries: 1,
    }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchMock.mock.calls[1][1].headers).toMatchObject({
      'Last-Event-ID': '7',
    });
    unmount();
  });

  it('clears the replay cursor after an empty SSE id', async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    const firstReader = {
      read: vi.fn()
        .mockResolvedValueOnce({
          done: false,
          value: encoder.encode('id:\ndata: {"value":"first"}\n\n'),
        })
        .mockRejectedValueOnce(new Error('stream dropped')),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const secondReader = {
      read: vi.fn(() => new Promise(() => undefined)),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => firstReader } })
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => secondReader } });
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('token', 'test-token');

    const { unmount } = renderHook(() => useEventStream({
      path: '/events', initialLastEventId: '7', maxRetries: 1,
    }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchMock.mock.calls[1][1].headers).not.toHaveProperty('Last-Event-ID');
    unmount();
  });

  it('ignores malformed JSON without ending the stream', async () => {
    const encoder = new TextEncoder();
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({
          done: false,
          value: encoder.encode('data: not-json\n\ndata: {"ok":true}\n\n'),
        })
        .mockResolvedValueOnce({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, body: { getReader: () => reader },
    }));
    localStorage.setItem('token', 'test-token');
    const onEvent = vi.fn();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    renderHook(() => useEventStream({ path: '/events', onEvent, autoReconnect: false }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledOnce());
    expect(onEvent.mock.calls[0][0].data).toEqual({ ok: true });
    expect(consoleError).toHaveBeenCalledOnce();
  });

  it('ignores empty heartbeat events without logging parse errors', async () => {
    const encoder = new TextEncoder();
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({
          done: false,
          value: encoder.encode('event: ping\ndata:\n\ndata: {"ok":true}\n\n'),
        })
        .mockResolvedValueOnce({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, body: { getReader: () => reader },
    }));
    localStorage.setItem('token', 'test-token');
    const onEvent = vi.fn();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    renderHook(() => useEventStream({ path: '/events', onEvent, autoReconnect: false }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledOnce());
    expect(onEvent.mock.calls[0][0].data).toEqual({ ok: true });
    expect(consoleError).not.toHaveBeenCalled();
  });
});
