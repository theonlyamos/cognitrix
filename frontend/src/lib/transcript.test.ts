import { describe, expect, it } from 'vitest';
import { parseChatEntries } from '@/lib/transcript';

describe('parseChatEntries tool results', () => {
  it('attaches paired results to tool calls without duplicating them and retains orphans', () => {
    const entries = parseChatEntries([
      {
        role: 'assistant',
        type: 'tool_calls',
        content: 'Checking both sources.',
        tool_calls: [{
          name: 'read_file',
          arguments: { path: 'README.md' },
          tool_call_id: 'call-paired',
        }],
      },
      {
        role: 'tool',
        tool_call_id: 'call-paired',
        content: 'paired contents',
      },
      {
        role: 'tool',
        tool_call_id: 'call-orphan',
        content: 'orphan contents',
      },
    ]);

    expect(entries).toEqual([
      {
        kind: 'tool_calls',
        content: 'Checking both sources.',
        name: undefined,
        tools: [{
          id: 'call-paired',
          name: 'read_file',
          args: '{"path":"README.md"}',
          result: 'paired contents',
          status: 'done',
        }],
      },
      { kind: 'tool_result', content: 'orphan contents' },
    ]);
  });
});
