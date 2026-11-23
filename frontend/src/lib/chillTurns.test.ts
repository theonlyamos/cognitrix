import { describe, expect, it } from 'vitest';
import { repliesOf, splitTurns, visibleMarks } from '@/lib/chillTurns';

const m = (role: string, content = 'x') => ({ role, content });
const isUser = (x: { role: string }) => x.role === 'user';

describe('splitTurns', () => {
  it('splits a chat into what was said and everything after it', () => {
    const chat = [m('assistant', 'hi'), m('user', 'a'), m('tool'), m('assistant', 'A'), m('user', 'b'), m('assistant', 'B')];
    const turns = splitTurns(chat, isUser);
    expect(turns).toEqual([
      { user: null, start: 0, end: 1 },
      { user: 1, start: 1, end: 4 },
      { user: 4, start: 4, end: 6 },
    ]);
    expect(repliesOf(chat, turns[1]).map((x) => x.content)).toEqual(['x', 'A']);
    expect(repliesOf(chat, turns[0]).map((x) => x.content)).toEqual(['hi']);
  });

  it('has no turns for an empty chat', () => {
    expect(splitTurns([], isUser)).toEqual([]);
    expect(repliesOf([], undefined)).toEqual([]);
  });
});

describe('visibleMarks', () => {
  it('shows every mark in a short chat and a window around the current one in a long chat', () => {
    expect(visibleMarks(7, 3)).toEqual({ from: 0, to: 7 });
    expect(visibleMarks(60, 0)).toEqual({ from: 0, to: 25 });
    expect(visibleMarks(60, 30)).toEqual({ from: 18, to: 43 });
    expect(visibleMarks(60, 59)).toEqual({ from: 35, to: 60 });
  });
});
