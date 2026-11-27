import { describe, expect, it } from 'vitest';
import { weekChange } from './weekChange';

describe('weekChange', () => {
  it('says nothing without a last week to compare with', () => {
    expect(weekChange(5, 0)).toEqual({ kind: 'none' });
  });
  it('tells a big jump as "up from" instead of a huge percent', () => {
    expect(weekChange(47_000_000, 168_000)).toEqual({ kind: 'from', previous: 168_000 });
  });
  it('gives a percent otherwise, capped at 999', () => {
    expect(weekChange(124, 100)).toEqual({ kind: 'percent', value: 24, up: true });
    expect(weekChange(50, 100)).toEqual({ kind: 'percent', value: 50, up: false });
    expect(weekChange(999, 100)).toEqual({ kind: 'percent', value: 899, up: true });
  });
});
