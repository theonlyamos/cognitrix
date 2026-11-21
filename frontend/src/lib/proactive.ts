import { api } from '@/lib/api';

export type ProactivityLevel = 'off' | 'brief' | 'balanced' | 'eager';

export interface Proactivity {
  level: ProactivityLevel;
  /** 'HH:MM' in your time zone. */
  quiet_start: string;
  quiet_end: string;
  /** Alerts a day that may buzz your phone or desktop (urgent ones don't count). */
  max_pushes: number;
  brief_time: string;
  /** Tokens a day for work you didn't ask for; 0 means no limit. */
  daily_tokens: number;
  timezone: string;
  browser_timezone: string;
  /** Topics you said never to bring up. */
  muted: string[];
  /** 1 normally; 2 or 4 after a run of "not useful" answers (check-ins slow down). */
  backoff: number;
  week: { tokens: number; pushes: number; held: number; runs: number; skipped: number };
  today_tokens: number;
  /** Dollars per million tokens at the main assistant's model price; null when unknown. */
  token_price?: number | null;
  main_assistant: { id: string; name: string; primary: boolean; checkins: boolean; every_minutes: number } | null;
  /** True when your assistant isn't your main one yet or its check-ins are off. */
  needs_setup: boolean;
  limits: { max_pushes: number; daily_tokens: number };
}

export async function getProactivity(): Promise<Proactivity> {
  return (await api.get<Proactivity>('/assistant/proactivity')).data;
}

export async function saveProactivity(input: Partial<Pick<Proactivity,
  'level' | 'quiet_start' | 'quiet_end' | 'max_pushes' | 'brief_time' | 'daily_tokens' | 'muted'>>): Promise<Proactivity> {
  return (await api.put<Proactivity>('/assistant/proactivity', input)).data;
}

/** Make your assistant your main one and turn its check-ins on. */
export async function enableProactive(): Promise<Proactivity> {
  return (await api.post<Proactivity>('/assistant/proactivity/enable')).data;
}

export type BriefSection = 'today' | 'reply' | 'waiting' | 'stalled' | 'offers' | 'other';
export type BriefVerdict = 'useful' | 'not_useful' | 'never';

export interface BriefItem {
  index: number;
  section: BriefSection;
  text: string;
  why?: string;
  link?: string;
  feedback: BriefVerdict | null;
}

export interface MorningBrief {
  id: string;
  agent_id: string | null;
  agent_name: string;
  /** Your local day, 'YYYY-MM-DD'. */
  day: string;
  summary: string;
  items: BriefItem[];
  sections: Record<BriefSection, string>;
  created_at: string | null;
}

export async function latestBrief(): Promise<MorningBrief | null> {
  return (await api.get<{ brief: MorningBrief | null }>('/assistant/brief')).data.brief;
}

/** Brief me now: start the morning brief right away. */
export async function briefNow(): Promise<{ task_id: string; run_id: string }> {
  return (await api.post<{ task_id: string; run_id: string }>('/assistant/brief')).data;
}

export async function briefFeedback(briefId: string, index: number, verdict: BriefVerdict): Promise<MorningBrief> {
  return (await api.post<{ brief: MorningBrief }>(`/assistant/briefs/${encodeURIComponent(briefId)}/feedback`, { index, verdict })).data.brief;
}

export interface SituationItem {
  kind: string;
  title: string;
  detail: string;
  key: string;
  urgent: boolean;
  blocking: boolean;
  href: string;
}

export interface Situation {
  local_time: string;
  meetings: SituationItem[];
  waiting: SituationItem[];
  stalled: SituationItem[];
  due: SituationItem[];
  unread_alerts: number;
}

export async function getSituation(): Promise<Situation> {
  return (await api.get<Situation>('/assistant/situation')).data;
}

/** "Next step: …" as the last line of a reply: the step, and the reply without that line. */
export function nextStep(text: string | null | undefined): { step: string; rest: string } | null {
  if (!text) return null;
  const m = /\n\s*\**Next step:?\**:?\s*(.+?)\s*$/i.exec(text.trimEnd());
  if (!m) return null;
  const step = m[1].replace(/\*+$/, '').trim();
  if (!step || step.length > 200) return null;
  return { step, rest: text.slice(0, m.index).trimEnd() };
}
