import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useVoiceRecorder } from './useVoiceRecorder';

describe('useVoiceRecorder', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('releases the mic when the component goes away during the permission prompt', async () => {
    const stopTrack = vi.fn();
    let grant: (s: MediaStream) => void = () => undefined;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn(() => new Promise<MediaStream>((r) => { grant = r; })) },
    });
    const AudioCtx = vi.fn();
    vi.stubGlobal('AudioContext', AudioCtx);

    const { result, unmount } = renderHook(() => useVoiceRecorder());
    let started: Promise<void> = Promise.resolve();
    act(() => { started = result.current.start(); });
    unmount();
    await act(async () => {
      grant({ getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream);
      await started;
    });

    expect(stopTrack).toHaveBeenCalled();
    expect(AudioCtx).not.toHaveBeenCalled();
  });

  it('cancel during the permission prompt leaves nothing recording', async () => {
    const stopTrack = vi.fn();
    let grant: (s: MediaStream) => void = () => undefined;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn(() => new Promise<MediaStream>((r) => { grant = r; })) },
    });
    const { result } = renderHook(() => useVoiceRecorder());
    let started: Promise<void> = Promise.resolve();
    act(() => { started = result.current.start(); });
    expect(result.current.state).toBe('starting');
    act(() => result.current.cancel());
    await act(async () => {
      grant({ getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream);
      await started;
    });
    expect(stopTrack).toHaveBeenCalled();
    expect(result.current.state).toBe('idle');
  });
});
