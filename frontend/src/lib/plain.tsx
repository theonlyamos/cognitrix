import { dateLocale, tr } from '@/lib/i18n';

// Plain words for things the server stores as codes: task and run statuses,
// schedules, times and costs. One place, so every screen says them the same way.

/** Server times are naive UTC ('YYYY-MM-DD HH:MM:SS') or ISO; null when unreadable. */
export function parseServerTime(stamp: string | null | undefined): Date | null {
  if (!stamp) return null;
  const iso = stamp.includes('T') ? stamp : stamp.replace(' ', 'T');
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

const STATUS_WORDS = {
  pending: { get label() { return tr('ui.lib.plain.notStarted'); } },
  queued: { get label() { return tr('ui.lib.plain.starting'); } },
  in_progress: { get label() { return tr('ui.lib.plain.running'); } },
  running: { get label() { return tr('ui.lib.plain.running'); } },
  completed: { get label() { return tr('ui.lib.plain.done'); } },
  done: { get label() { return tr('ui.lib.plain.done'); } },
  failed: { get label() { return tr('ui.lib.plain.failed'); } },
  cancelled: { get label() { return tr('ui.lib.plain.stopped'); } },
  canceled: { get label() { return tr('ui.lib.plain.stopped'); } },
  paused: { get label() { return tr('ui.lib.plain.paused'); } },
  waiting: { get label() { return tr('ui.lib.plain.needsYou'); } },
  blocked: { get label() { return tr('ui.lib.plain.needsYou'); } },
  skipped: { get label() { return tr('ui.lib.plain.skipped'); } },
  scheduled: { get label() { return tr('ui.lib.plain.scheduled'); } },
};

/** "in_progress" -> "Running". Unknown codes come back with underscores as spaces. */
export function statusLabel(status: string | null | undefined): string {
  const key = String(status || 'pending').toLowerCase() as keyof typeof STATUS_WORDS;
  return STATUS_WORDS[key]?.label ?? String(status).replace(/_/g, ' ');
}

/** Tailwind classes for a status pill. */
export function statusTone(status: string | null | undefined): string {
  switch (String(status || 'pending').toLowerCase()) {
    case 'completed': case 'done': return 'text-ok border-ok/40';
    case 'in_progress': case 'running': case 'queued': return 'text-accent-ink border-accent/40';
    case 'failed': return 'text-danger-ink border-danger/40';
    case 'waiting': case 'blocked': return 'text-warn-ink border-warn/40';
    default: return 'text-fg-dim border-line';
  }
}

const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString();

/** "9:00", "tomorrow 9:00", "Mon 23 Nov, 9:00": no seconds, in the person's time zone. */
export function friendlyTime(stamp: string | Date | null | undefined, now = new Date()): string {
  const date = stamp instanceof Date ? stamp : parseServerTime(stamp);
  if (!date) return '';
  const locale = dateLocale();
  const clock = date.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' });
  if (sameDay(date, now)) return clock;
  const tomorrow = new Date(now.getTime() + 86400000);
  const yesterday = new Date(now.getTime() - 86400000);
  const words = { tomorrow: { label: 'tomorrow' }, yesterday: { label: 'yesterday' } };
  if (sameDay(date, tomorrow)) return `${words.tomorrow.label} ${clock}`;
  if (sameDay(date, yesterday)) return `${words.yesterday.label} ${clock}`;
  const day = date.toLocaleDateString(locale, {
    weekday: 'short', day: 'numeric', month: 'short',
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  });
  return `${day}, ${clock}`;
}

/** "in 2 h", "3 min ago", "in 2 days": for a time near now. */
export function relativeTime(stamp: string | Date | null | undefined, now = Date.now()): string {
  const date = stamp instanceof Date ? stamp : parseServerTime(stamp);
  if (!date) return '';
  const seconds = Math.round((date.getTime() - now) / 1000);
  const abs = Math.abs(seconds);
  let format: Intl.RelativeTimeFormat;
  try {
    format = new Intl.RelativeTimeFormat(dateLocale(), { numeric: 'auto', style: 'short' });
  } catch {
    return friendlyTime(date);
  }
  if (abs < 60) return format.format(0, 'minute');
  if (abs < 3600) return format.format(Math.round(seconds / 60), 'minute');
  if (abs < 86400) return format.format(Math.round(seconds / 3600), 'hour');
  if (abs < 86400 * 30) return format.format(Math.round(seconds / 86400), 'day');
  return friendlyTime(date);
}

/** The clock time to show for a cron line: the next run's own time when known. */
function cronClock(hour: number, minute: number, nextRun: Date | null): string {
  const at = nextRun ?? new Date(2023, 0, 1, hour, minute);
  return at.toLocaleTimeString(dateLocale(), { hour: 'numeric', minute: '2-digit' });
}

function weekdayName(day: number, style: 'long' | 'short' = 'long'): string {
  // 2023-01-01 was a Sunday.
  return new Date(2023, 0, 1 + (((day % 7) + 7) % 7)).toLocaleDateString(dateLocale(), { weekday: style });
}

function everySeconds(seconds: number): string {
  const units = {
    minutes: { label: tr('ui.lib.plain.everyNMinutes') }, hour: { label: tr('ui.lib.plain.everyHour') }, hours: { label: tr('ui.lib.plain.everyNHours') },
    day: { label: tr('ui.lib.plain.everyDay') }, days: { label: tr('ui.lib.plain.everyNDays') }, week: { label: tr('ui.lib.plain.everyWeek') },
  };
  const fill = (text: string, n: number) => text.replace('{n}', String(n));
  if (seconds % 604800 === 0 && seconds / 604800 === 1) return units.week.label;
  if (seconds % 86400 === 0) return seconds === 86400 ? units.day.label : fill(units.days.label, seconds / 86400);
  if (seconds % 3600 === 0) return seconds === 3600 ? units.hour.label : fill(units.hours.label, seconds / 3600);
  return fill(units.minutes.label, Math.max(1, Math.round(seconds / 60)));
}

/**
 * A cron line in words when it is one of the common shapes ("0 9 * * 1" ->
 * "Every Monday at 9:00"); otherwise null. The server reads cron lines in its
 * own time zone, so pass the task's next run when there is one: its clock
 * time and weekday are shown in the person's time zone.
 */
export function cronInWords(cron: string, nextRun: Date | null = null): string | null {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
  const words = {
    everyNMinutes: { label: tr('ui.lib.plain.everyNMinutes') }, everyHour: { label: tr('ui.lib.plain.everyHour') },
    everyNHours: { label: tr('ui.lib.plain.everyNHours') }, dailyAt: { label: tr('ui.lib.plain.everyDayAtTime') },
    weekdaysAt: { label: tr('ui.lib.plain.weekdaysAtTime') }, weeklyAt: { label: tr('ui.lib.plain.everyDayAtTime_3186') },
    monthlyAt: { label: tr('ui.lib.plain.dayNOfEveryMonth') },
  };
  if (month !== '*') return null;
  const stepMinutes = /^\*\/(\d+)$/.exec(minute);
  if (stepMinutes && hour === '*' && dayOfMonth === '*' && dayOfWeek === '*') {
    return words.everyNMinutes.label.replace('{n}', stepMinutes[1]);
  }
  if (!/^\d+$/.test(minute)) return null;
  const stepHours = /^\*\/(\d+)$/.exec(hour);
  if (dayOfMonth === '*' && dayOfWeek === '*') {
    if (hour === '*') return words.everyHour.label;
    if (stepHours) return words.everyNHours.label.replace('{n}', stepHours[1]);
  }
  if (!/^\d+$/.test(hour)) return null;
  const clock = cronClock(Number(hour), Number(minute), nextRun);
  if (dayOfMonth === '*' && dayOfWeek === '*') return words.dailyAt.label.replace('{time}', clock);
  if (dayOfMonth === '*' && (dayOfWeek === '1-5' || dayOfWeek.toLowerCase() === 'mon-fri')) {
    return words.weekdaysAt.label.replace('{time}', clock);
  }
  if (dayOfMonth === '*' && /^[0-7]$/.test(dayOfWeek)) {
    const day = nextRun ? nextRun.toLocaleDateString(dateLocale(), { weekday: 'long' }) : weekdayName(Number(dayOfWeek));
    return words.weeklyAt.label.replace('{day}', day).replace('{time}', clock);
  }
  if (dayOfMonth === '*' && /^[0-7](,[0-7])+$/.test(dayOfWeek)) {
    const days = dayOfWeek.split(',').map((d) => weekdayName(Number(d), 'short'));
    return words.weeklyAt.label.replace('{day}', days.join(', ')).replace('{time}', clock);
  }
  if (/^\d+$/.test(dayOfMonth) && dayOfWeek === '*') {
    return words.monthlyAt.label.replace('{n}', dayOfMonth).replace('{time}', clock);
  }
  return null;
}

export interface ScheduleFields {
  next_run_at?: string | null;
  schedule_cron?: string | null;
  schedule_interval?: number | null;
  schedule_at?: string | null;
}

/** A task's schedule in words, or null when it has none. */
export function scheduleInWords(task: ScheduleFields): string | null {
  const words = { once: { label: tr('ui.lib.plain.onceTime') }, custom: { label: tr('ui.lib.plain.customScheduleCron') } };
  if (task.schedule_cron) {
    return cronInWords(task.schedule_cron, parseServerTime(task.next_run_at)) ?? words.custom.label.replace('{cron}', task.schedule_cron);
  }
  if (task.schedule_interval) return everySeconds(task.schedule_interval);
  if (task.schedule_at) return words.once.label.replace('{time}', friendlyTime(task.schedule_at));
  return null;
}

/** "$0.04", "under 1¢", "$12.30". */
export function money(usd: number | string | null | undefined): string {
  const value = Number(usd);
  if (!Number.isFinite(value) || value <= 0) return '';
  const words = { tiny: { label: 'under 1¢' } };
  if (value < 0.01) return words.tiny.label;
  return `$${value.toFixed(2)}`;
}

/** "2 min 25 s", "48 s", "1 h 4 min". */
export function tookWords(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return tr('ui.lib.plain.sS', { s });
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ''}`;
  return tr('ui.lib.plain.floorHFloor2Min', { floor: Math.floor(s / 3600), floor2: Math.floor((s % 3600) / 60) });
}

/** "637k", "1.2M": a token count for the Details line. */
export function compactNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

const MODEL_WORDS: Record<string, string> = {
  gpt: 'GPT', oss: 'OSS', claude: 'Claude', gemini: 'Gemini', grok: 'Grok', qwen: 'Qwen', llama: 'Llama',
  deepseek: 'DeepSeek', kimi: 'Kimi', mistral: 'Mistral', glm: 'GLM',
};

/**
 * A model id as a name: "claude-sonnet-5-5" -> "Claude Sonnet 5.5",
 * "openai/gpt-6.2-sol" -> "GPT-6.2 Sol". Ids it can't read come back as they are.
 */
export function modelName(id: string | null | undefined): string {
  if (!id) return '';
  const bare = id.includes('/') ? id.slice(id.lastIndexOf('/') + 1) : id;
  const parts = bare.split('-').filter(Boolean);
  if (parts.length === 0 || !/^[a-z]/.test(parts[0])) return id;
  const words: string[] = [];
  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i];
    // "5-5" is a version written with a dash: 5.5.
    if (/^\d+$/.test(part) && /^\d+$/.test(parts[i + 1] ?? '') && part.length <= 2 && parts[i + 1].length <= 2) {
      words.push(`${part}.${parts[i + 1]}`);
      i += 1;
    } else if (/^\d{6,}$/.test(part)) {
      // A date stamp at the end of an id says nothing to a person.
    } else {
      words.push(MODEL_WORDS[part] ?? (/^\d/.test(part) ? part : part[0].toUpperCase() + part.slice(1)));
    }
  }
  const name = words.join(' ');
  return name.replace(/^GPT (\d)/, 'GPT-$1');
}
