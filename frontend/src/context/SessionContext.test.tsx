import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';

import { SessionProvider, useSession } from './SessionContext';

function wrapper({ children }: { children: ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}

describe('SessionContext tool calls', () => {
  it('attaches reconciled upload artifacts to the latest user message', () => {
    const { result } = renderHook(() => useSession(), { wrapper });

    act(() => {
      result.current.addMessage('user', 'Please edit this image.');
      result.current.addMessage('assistant', 'Working on it.');
      result.current.attachArtifactsToLatestUser([
        { id: 'uploaded-image-1', mime_type: 'image/png', filename: 'source.png', origin: 'uploaded' },
      ]);
    });

    expect(result.current.messages[0].artifacts).toEqual([
      { id: 'uploaded-image-1', mime_type: 'image/png', filename: 'source.png', origin: 'uploaded' },
    ]);
    expect(result.current.messages[1].artifacts).toBeUndefined();
  });

  it('renders a terminal error when the matching start event is absent', () => {
    const { result } = renderHook(() => useSession(), { wrapper });

    act(() => {
      result.current.resolveToolCall('Malformed tool call', 'error', {
        id: 'malformed-1',
        result: 'Error: malformed tool call (no name)',
      });
    });

    expect(result.current.messages).toEqual([
      expect.objectContaining({
        role: 'tool',
        tools: [{
          id: 'malformed-1',
          name: 'Malformed tool call',
          status: 'error',
          result: 'Error: malformed tool call (no name)',
        }],
      }),
    ]);
  });

  it('ignores an unmatched normal completion replay', () => {
    const { result } = renderHook(() => useSession(), { wrapper });

    act(() => {
      result.current.resolveToolCall('Search docs', 'done', {
        id: 'tool-1',
        result: 'Found relevant documentation',
      });
    });

    expect(result.current.messages).toEqual([]);
  });

  it('keeps an unmatched successful artifact completion visible', () => {
    const { result } = renderHook(() => useSession(), { wrapper });

    act(() => {
      result.current.resolveToolCall('Generate Image', 'done', {
        id: 'image-1',
        result: 'Image generated.',
        artifacts: [{ id: 'artifact-1', mime_type: 'image/png', filename: 'image.png' }],
      });
    });

    expect(result.current.messages).toEqual([
      expect.objectContaining({
        role: 'tool',
        tools: [expect.objectContaining({
          id: 'image-1',
          name: 'Generate Image',
          status: 'done',
          artifacts: [{ id: 'artifact-1', mime_type: 'image/png', filename: 'image.png' }],
        })],
      }),
    ]);
  });

  it('marks only running tools as stopped while preserving completed tools', () => {
    const { result } = renderHook(() => useSession(), { wrapper });

    act(() => {
      result.current.addToolCall('Generate Image', { id: 'image-1' });
      result.current.addToolCall('Search docs', { id: 'search-1' });
      result.current.resolveToolCall('Search docs', 'done', {
        id: 'search-1',
        result: 'Found it',
      });
      result.current.stopRunningTools();
    });

    expect(result.current.messages[0].tools).toEqual([
      expect.objectContaining({
        id: 'image-1',
        status: 'stopped',
        result: 'Stopped by user.',
      }),
      expect.objectContaining({
        id: 'search-1',
        status: 'done',
        result: 'Found it',
      }),
    ]);
  });

  it('marks only running tools as failed after a terminal turn error', () => {
    const { result } = renderHook(() => useSession(), { wrapper });

    act(() => {
      result.current.addToolCall('Search docs', { id: 'search-1' });
      result.current.failRunningTools('Provider disconnected');
    });

    expect(result.current.messages[0].tools).toEqual([
      expect.objectContaining({
        id: 'search-1',
        status: 'error',
        result: 'Provider disconnected',
      }),
    ]);
  });
});
