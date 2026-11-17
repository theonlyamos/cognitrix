import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ChatMessageRow } from '@/components/ChatMessageRow';
import { fmtTime, fmtTook } from '@/lib/messageTime';

describe('reply time and duration', () => {
  it('formats durations compactly', () => {
    expect(fmtTook(4230)).toBe('4.2s');
    expect(fmtTook(42_000)).toBe('42s');
    expect(fmtTook(185_000)).toBe('3m 05s');
    expect(fmtTook(3_720_000)).toBe('1h 02m');
  });

  it('adds the date for a reply from another day', () => {
    const now = new Date('2026-11-17T12:00:00');
    expect(fmtTime(new Date('2026-11-17T09:05:00').toISOString(), now)).not.toMatch(/Nov/);
    expect(fmtTime(new Date('2026-11-12T09:05:00').toISOString(), now)).toMatch(/Nov/);
  });

  it('shows when an agent reply was sent and how long it took beside its actions', () => {
    render(
      <ChatMessageRow
        message={{ id: 'a1', role: 'assistant', content: 'Done.', timestamp: new Date().toISOString(), durationMs: 12_500 }}
        isLast
        streaming={false}
        onRegenerate={() => {}}
      />,
    );
    expect(screen.getByText('took 13s')).toBeTruthy();
    expect(screen.getByTitle('Answered in 13s')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'regenerate' })).toBeTruthy();
  });
});
