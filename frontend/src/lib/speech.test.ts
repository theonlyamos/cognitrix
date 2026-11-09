import { describe, expect, it } from 'vitest';
import { speakableText, speechPieces } from './speech';

describe('speakableText', () => {
  it('drops code, reasoning and markup, and keeps the words', () => {
    const text = speakableText([
      '<think>secret plan</think>',
      '## Result',
      'Here is **the answer** with a [link](https://example.com/x) and `inline`.',
      '```python',
      'print("hi")',
      '```',
      '- first',
      '- second',
    ].join('\n'));
    expect(text).not.toMatch(/secret|print|https|\*\*|##|`/);
    expect(text).toContain('Result');
    expect(text).toContain('Here is the answer with a link and inline.');
    expect(text).toContain('(code omitted)');
    expect(text).toContain('first');
  });
});

describe('speechPieces', () => {
  it('cuts long text at sentence ends and loses nothing', () => {
    const sentence = 'This is one sentence of the reply. ';
    const text = sentence.repeat(60).trim();
    const pieces = speechPieces(text, 200);
    expect(pieces.length).toBeGreaterThan(5);
    expect(pieces.every((p) => p.length <= 200 && p.endsWith('.'))).toBe(true);
    expect(pieces.join(' ')).toBe(text);
  });

  it('keeps short text whole', () => {
    expect(speechPieces('Hello there.')).toEqual(['Hello there.']);
  });
});
