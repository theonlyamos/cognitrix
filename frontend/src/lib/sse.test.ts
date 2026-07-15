import { describe, expect, it } from 'vitest';
import { consumeSSE } from '@/lib/sse';

describe('consumeSSE', () => {
  it('parses ids, event names, comments, and multi-line data', () => {
    const input = [
      ': heartbeat',
      'id: 7',
      'event: task_run',
      'data: {"part":"one"',
      'data: ,"part2":"two"}',
      '',
      '',
    ].join('\n');

    expect(consumeSSE(input)).toEqual({
      frames: [{
        id: '7',
        event: 'task_run',
        data: '{"part":"one"\n,"part2":"two"}',
      }],
      rest: '',
    });
  });

  it('keeps an incomplete frame for the next network chunk', () => {
    const first = consumeSSE('id: 2\ndata: {"value":');
    expect(first.frames).toEqual([]);
    const second = consumeSSE(first.rest + '1}\n\n');
    expect(second.frames).toEqual([{ id: '2', data: '{"value":1}' }]);
  });
});
