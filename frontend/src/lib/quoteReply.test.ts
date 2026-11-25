import { describe, expect, it } from 'vitest';
import { askLabel, cleanQuote, MAX_QUOTE_CHARS, splitQuotes, withQuotes } from '@/lib/quoteReply';

describe('quote reply', () => {
  it('puts quotes on top of the message as blockquotes', () => {
    expect(withQuotes('why?', ['line one\nline two'])).toBe('> line one\n> line two\n\nwhy?');
    expect(withQuotes('and this', ['a', 'b'])).toBe('> a\n\n> b\n\nand this');
    expect(withQuotes('no quotes', [])).toBe('no quotes');
  });

  it('keeps blank lines inside a quote as part of it', () => {
    const sent = withQuotes('ok', ['first\n\nsecond']);
    expect(sent).toBe('> first\n>\n> second\n\nok');
    expect(splitQuotes(sent)).toEqual({ quotes: ['first\n\nsecond'], text: 'ok' });
  });

  it('splits a sent message back into quotes and words', () => {
    expect(splitQuotes(withQuotes('why?', ['a', 'b c']))).toEqual({ quotes: ['a', 'b c'], text: 'why?' });
    expect(splitQuotes('plain message')).toEqual({ quotes: [], text: 'plain message' });
    expect(splitQuotes('text first\n\n> later quote')).toEqual({ quotes: [], text: 'text first\n\n> later quote' });
  });

  it('tidies and caps picked text', () => {
    expect(cleanQuote('  hi  \r\n\n\n\nthere ')).toBe('hi\n\nthere');
    expect(cleanQuote('x'.repeat(MAX_QUOTE_CHARS + 50))).toHaveLength(MAX_QUOTE_CHARS + 1);
  });

  it('labels a message by its words, or its quote when there are none', () => {
    expect(askLabel(withQuotes('why?', ['a']))).toBe('why?');
    expect(askLabel('> only a quote')).toBe('“only a quote”');
  });
});
