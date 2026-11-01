import { describe, expect, it } from 'vitest';
import { handleFor, scheduleText, timeUntil } from '@/lib/agentDraft';

describe('agent page helpers', () => {
  const now = Date.parse('2026-11-01T12:00:00Z');

  it('rounds countdowns without showing 60 minutes', () => {
    expect(timeUntil('2026-11-02T11:59:50', now)).toBe('in 24 h');
    expect(timeUntil('2026-11-01T14:30:00', now)).toBe('in 2 h 30 min');
    expect(timeUntil('2026-11-01T12:20:00', now)).toBe('in 20 min');
    expect(timeUntil('2026-11-01T12:00:30', now)).toBe('any moment');
  });

  it('describes schedules in plain words', () => {
    expect(scheduleText({ schedule_enabled: false })).toBe('when you run it');
    expect(scheduleText({ schedule_enabled: true, schedule_interval: 86400 })).toBe('every day');
    expect(scheduleText({ schedule_enabled: true, schedule_interval: 7200 })).toBe('every 2 hours');
    expect(scheduleText({ schedule_enabled: true, schedule_cron: '0 9 * * 1' })).toBe('cron 0 9 * * 1');
  });

  it('makes the mention handle from the name', () => {
    expect(handleFor('Research Lead')).toBe('@Research-Lead');
  });
});
