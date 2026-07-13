import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';

import { SessionProvider, useSession } from './SessionContext';

function wrapper({ children }: { children: ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}

describe('SessionContext tool calls', () => {
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
});
