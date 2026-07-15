import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSSE } from '@/hooks/useSSE';

describe('useSSE cleanup', () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('uses one stable stream id for the SSE connection and reconnects', async () => {
    const reader = {
      cancel: vi.fn().mockResolvedValue(undefined),
      read: vi.fn(() => new Promise(() => undefined)),
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => reader },
    });
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('token', 'test-token');

    const { result } = renderHook(() => useSSE({ agentId: 'agent-1', autoReconnect: false }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());

    expect(result.current.streamId).toBeTruthy();
    const firstUrl = String(fetchMock.mock.calls[0][0]);
    expect(firstUrl).toContain(`agent_id=agent-1`);
    expect(firstUrl).toContain(`stream_id=${encodeURIComponent(result.current.streamId)}`);

    act(() => result.current.reconnect());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[1][0])).toContain(
      `stream_id=${encodeURIComponent(result.current.streamId)}`,
    );
  });

  it('forwards the stopped-turn terminal event to the chat consumer', async () => {
    const encoder = new TextEncoder();
    const reader = {
      cancel: vi.fn().mockResolvedValue(undefined),
      read: vi.fn()
        .mockResolvedValueOnce({
          done: false,
          value: encoder.encode('data: {"type":"turn_stopped","session_id":"session-1"}\n\n'),
        })
        .mockResolvedValueOnce({ done: true, value: undefined }),
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => reader },
    }));
    localStorage.setItem('token', 'test-token');
    const onMessage = vi.fn();

    renderHook(() => useSSE({ onMessage, autoReconnect: false }));

    await waitFor(() => expect(onMessage).toHaveBeenCalledWith({
      type: 'turn_stopped',
      session_id: 'session-1',
    }));
  });

  it('consumes pending read and cancellation rejections when the connection unmounts', async () => {
    const cancelCatch = vi.fn().mockResolvedValue(undefined);
    const readCatch = vi.fn();
    const readThen = vi.fn(() => ({ catch: readCatch }));
    const reader = {
      cancel: vi.fn(() => ({ catch: cancelCatch })),
      read: vi.fn(() => ({ then: readThen })),
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => reader },
    }));
    localStorage.setItem('token', 'test-token');

    const { unmount } = renderHook(() => useSSE({ autoReconnect: false }));
    await waitFor(() => expect(reader.read).toHaveBeenCalledOnce());

    unmount();

    expect(readCatch).toHaveBeenCalledOnce();
    expect(reader.cancel).toHaveBeenCalledOnce();
    expect(cancelCatch).toHaveBeenCalledOnce();
  });

  it('stops reconnecting after repeated read failures reach maxRetries', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const readError = new Error('stream failed');
    const readers: Array<{ read: ReturnType<typeof vi.fn>; cancel: ReturnType<typeof vi.fn> }> = [];
    const fetchMock = vi.fn(async () => {
      const reader = {
        read: vi.fn().mockRejectedValue(readError),
        cancel: vi.fn().mockResolvedValue(undefined),
      };
      readers.push(reader);
      return { ok: true, body: { getReader: () => reader } };
    });
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('token', 'test-token');
    const onError = vi.fn();

    const { result, unmount } = renderHook(() => useSSE({ maxRetries: 2, onError }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(onError).toHaveBeenCalledOnce();
    expect(result.current.error).toBe(readError);

    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(readers).toHaveLength(3);
    unmount();
  });
});
