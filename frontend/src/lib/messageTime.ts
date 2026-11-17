import { dateLocale } from '@/lib/i18n';

/** "14:05" today, "12 Nov, 14:05" on another day. */
export const fmtTime = (value?: string | number, now: Date = new Date()) => {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const time = d.toLocaleTimeString(dateLocale(), { hour: '2-digit', minute: '2-digit', hour12: false });
  if (d.toDateString() === now.toDateString()) return time;
  const date = d.toLocaleDateString(dateLocale(), {
    day: 'numeric',
    month: 'short',
    ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}),
  });
  return `${date}, ${time}`;
};

export const fmtFullTime = (value: string | number) => {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(dateLocale(), { dateStyle: 'full', timeStyle: 'medium' });
};

/** "4.2s", "42s", "3m 05s", "1h 02m". */
export const fmtTook = (ms: number) => {
  const secs = Math.max(0, ms) / 1000;
  if (secs < 10) return `${(Math.round(secs * 10) / 10).toFixed(1)}s`;
  const whole = Math.round(secs);
  if (whole < 60) return `${whole}s`;
  const mins = Math.floor(whole / 60);
  if (mins < 60) return `${mins}m ${String(whole % 60).padStart(2, '0')}s`;
  return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, '0')}m`;
};
