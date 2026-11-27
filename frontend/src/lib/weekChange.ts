/**
 * How this week compares with last week, for the dashboard's usage tile.
 * A percent stops meaning much when last week was tiny (47M against 168k
 * reads "27999%"), so a jump of ten times or more is told as "up from X"
 * instead, and any other percent is capped at 999.
 */
export type WeekChange =
  | { kind: 'none' }
  | { kind: 'from'; previous: number }
  | { kind: 'percent'; value: number; up: boolean };

export function weekChange(thisWeek: number, lastWeek: number): WeekChange {
  if (!(lastWeek > 0)) return { kind: 'none' };
  if (thisWeek >= lastWeek * 10) return { kind: 'from', previous: lastWeek };
  const change = Math.round(((thisWeek - lastWeek) / lastWeek) * 100);
  return { kind: 'percent', value: Math.min(Math.abs(change), 999), up: change > 0 };
}
