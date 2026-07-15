import { describe, expect, it } from 'vitest';
import { parseChatEntries, toChatMessages } from '@/lib/transcript';

describe('parseChatEntries tool results', () => {
  it('attaches paired results without duplicating them and retains orphans', () => {
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
      { role: 'tool', tool_call_id: 'call-paired', content: 'paired contents' },
      { role: 'tool', tool_call_id: 'call-orphan', content: 'orphan contents' },
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
          artifacts: undefined,
        }],
      },
      { kind: 'tool_result', content: 'orphan contents' },
    ]);
  });

  it('preserves failed tool status and valid artifacts in restored chat', () => {
    const entries = parseChatEntries([
      {
        role: 'assistant', type: 'tool_calls', content: '',
        tool_calls: [{ name: 'generate_image', arguments: {}, tool_call_id: 'call-1' }],
      },
      {
        role: 'tool', tool_call_id: 'call-1', content: 'provider failed',
        outcome: {
          status: 'error',
          artifacts: [
            { id: 'good', mime_type: 'image/png', filename: 'good.png' },
            { id: '', mime_type: 7 as unknown as string },
          ],
        },
      },
    ]);
    const messages = toChatMessages(entries);
    expect(messages[0].tools?.[0].status).toBe('error');
    expect(messages[0].tools?.[0].artifacts).toEqual([
      { id: 'good', mime_type: 'image/png', filename: 'good.png' },
    ]);
  });
});
