/**
 * Chill mode shows one turn at a time: something the person said and
 * everything the assistant did after it. This splits a chat into those turns.
 */
export interface TurnRange {
  /** Index of the person's message, or null for replies before any (a greeting). */
  user: number | null;
  /** First message of the turn (the person's message when there is one). */
  start: number;
  /** One past the last message of the turn. */
  end: number;
}

export function splitTurns<T>(messages: T[], isUserTurn: (message: T) => boolean): TurnRange[] {
  const turns: TurnRange[] = [];
  messages.forEach((message, index) => {
    if (isUserTurn(message)) {
      const last = turns[turns.length - 1];
      if (last) last.end = index;
      turns.push({ user: index, start: index, end: messages.length });
    } else if (turns.length === 0) {
      turns.push({ user: null, start: 0, end: messages.length });
    }
  });
  return turns;
}

/** The assistant's part of a turn: everything after the person's message. */
export function repliesOf<T>(messages: T[], turn: TurnRange | undefined): T[] {
  if (!turn) return [];
  return messages.slice(turn.user === null ? turn.start : turn.user + 1, turn.end);
}

/** Which marks to draw on the right edge, so a long chat still fits. */
export function visibleMarks(count: number, current: number, max = 25): { from: number; to: number } {
  if (count <= max) return { from: 0, to: count };
  const half = Math.floor(max / 2);
  const from = Math.min(Math.max(0, current - half), count - max);
  return { from, to: from + max };
}

/** One turn: what the person said, when, and what it made. */
export interface ChillTurn {
  key: string;
  question: string;
  at?: string | null;
  made?: 'page' | 'file' | 'app' | null;
  madeName?: string | null;
}

export function turnTime(at?: string | null): string {
  if (!at) return '';
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return '';
  const today = new Date();
  const sameDay = when.toDateString() === today.toDateString();
  return sameDay
    ? when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : when.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}
