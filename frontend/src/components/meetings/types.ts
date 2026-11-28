import { dateLocale, tr } from '@/lib/i18n';

/** What answers in a call may use: only the call, or also the person's work. */
export type ShareLevel = 'call' | 'work';
export type Status = 'queued' | 'transcribing' | 'writing' | 'done' | 'error' | 'joining' | 'waiting' | 'live';
export type ReplyMode = 'chat' | 'voice' | 'off';

export interface MeetingSummary {
  id: string;
  title: string;
  filename: string;
  agent_id: string;
  status: Status;
  progress: string;
  seconds: number;
  error: string;
  created_at: string;
  has_transcript: boolean;
  /** 'live': an agent sat in a call; 'recording': an upload. */
  kind?: 'recording' | 'live';
  url?: string;
  platform?: string;
  bot_name?: string;
  reply_mode?: ReplyMode;
  answers?: number;
  /** The live meeting worker is running, including while it writes notes after the call. */
  in_call?: boolean;
  /** What answers in the call may use. */
  share?: ShareLevel;
  /** Off the record: nothing said is kept until someone resumes. */
  paused?: boolean;
  /** The agent asked the person this during the call and is waiting for the answer. */
  owner_question?: string;
  rejoins?: number;
  /** A practice call on the agent's own computer. */
  test?: boolean;
}

export interface Meeting extends MeetingSummary {
  transcript: string;
  notes: string;
  context?: string;
  watch_words?: string[];
  guidance?: string[];
  /** Running notes, while the call goes on. */
  live_notes?: string;
  /** The whole call again with the speakers told apart. */
  speaker_transcript?: string;
  action_items?: { owner: string; what: string }[];
  /** What the agent could read on its screen when joining failed. */
  seen?: string;
  has_seen_image?: boolean;
}

export interface LiveReadiness {
  /** The agents' sandboxed computer is on: calls run in its browser. */
  computer: boolean;
  /** A read-aloud provider has a key, so answers can be spoken. */
  speech: boolean;
}

export interface AgentLite { id: string; name: string }

export const IN_CALL: Status[] = ['joining', 'waiting', 'live'];
export const working = (status: Status) => status === 'queued' || status === 'transcribing' || status === 'writing'
  || IN_CALL.includes(status);
export const PLATFORM: Record<string, string> = { meet: 'Google Meet', teams: 'Teams', zoom: 'Zoom' };

/** The platform a meeting link is for, from its address. */
export function platformOf(url: string): string {
  const host = (() => { try { return new URL(url.trim()).hostname.toLowerCase(); } catch { return ''; } })();
  if (host === 'meet.google.com') return 'meet';
  if (host.endsWith('teams.microsoft.com') || host.endsWith('teams.live.com')) return 'teams';
  if (host === 'zoom.us' || host.endsWith('.zoom.us')) return 'zoom';
  return '';
}

export function length(seconds: number): string {
  if (!seconds) return '';
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return tr('ui.meetings.roundSec', { round: Math.round(seconds) });
  return minutes >= 60 ? tr('ui.meetings.floorHValueMin', { floor: Math.floor(minutes / 60), value: minutes % 60 }) : tr('ui.meetings.minutesMin', { minutes });
}

export function when(stamp: string): Date | null {
  const date = new Date(stamp.includes('T') ? stamp : `${stamp.replace(' ', 'T')}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function day(stamp: string): string {
  const date = when(stamp);
  return date ? date.toLocaleDateString(dateLocale(), { month: 'short', day: 'numeric', year: 'numeric' }) : '';
}

export function clock(stamp: string): string {
  const date = when(stamp);
  return date ? date.toLocaleTimeString(dateLocale(), { hour: '2-digit', minute: '2-digit' }) : '';
}

/** Today, Yesterday or a short date, for list group headings and meta lines. */
export function dayGroup(stamp: string, now = new Date()): 'today' | 'yesterday' | 'earlier' {
  const date = when(stamp);
  if (!date) return 'earlier';
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (date.getTime() >= start) return 'today';
  if (date.getTime() >= start - 86_400_000) return 'yesterday';
  return 'earlier';
}
