import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CalendarDays, MoreHorizontal, RefreshCw } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Menu, MenuItem } from '@/components/meetings/Overlay';
import { cn } from '@/lib/utils';
import { dateLocale, tr } from '@/lib/i18n';

export type CallStatus = 'suggested' | 'approved' | 'skipped' | 'joined' | 'missed' | 'failed' | 'cancelled';
export type CallAction = 'send' | 'skip' | 'always' | 'never' | 'ask';

export interface CalendarCall {
  id: string;
  title: string;
  starts_at: string;
  ends_at: string;
  url: string;
  platform: string;
  organizer: string;
  organised_by_me: boolean;
  series: boolean;
  status: CallStatus;
  decided_by: string;
  meeting_id: string;
  error: string;
}

export interface CalendarSettings {
  enabled: boolean;
  agent_id: string;
  auto_join: 'ask' | 'mine';
  never_words: string;
  remind_minutes: number;
  series_choices: number;
  checked_at: string;
  error: string;
}

export interface CalendarCalls {
  connected: boolean;
  settings: CalendarSettings;
  calls: CalendarCall[];
}

export type CalendarRules = Pick<CalendarSettings, 'enabled' | 'agent_id' | 'auto_join' | 'never_words' | 'remind_minutes'>;

const PLATFORM: Record<string, string> = { meet: 'Google Meet', teams: 'Teams', zoom: 'Zoom' };

/** "Mon 09:30" in the person's own time zone (the server stores UTC). */
function callTime(stamp: string): string {
  const date = new Date(`${stamp.replace(' ', 'T')}Z`);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(dateLocale(), { weekday: 'short', hour: '2-digit', minute: '2-digit' });
}

function statusLabel(call: CalendarCall): string {
  switch (call.status) {
    case 'suggested': return tr('ui.upcomingCalls.notDecided');
    case 'approved': return call.decided_by === 'series' ? tr('ui.upcomingCalls.anAgentJoinsWholeSeries') : call.decided_by === 'rule' ? tr('ui.upcomingCalls.anAgentJoinsYourRule') : tr('ui.upcomingCalls.anAgentJoins');
    case 'skipped': return call.decided_by === 'series' ? tr('ui.upcomingCalls.skippedWholeSeries') : call.decided_by === 'rule' ? tr('ui.upcomingCalls.skippedYourRule') : 'Skipped';
    case 'joined': return tr('ui.upcomingCalls.agentSent');
    case 'missed': return 'Missed';
    case 'failed': return tr('ui.upcomingCalls.couldnTJoin');
    case 'cancelled': return 'Cancelled';
    default: return call.status;
  }
}

/** The calendar calls and the person's call rules, loaded once for the page
 *  and shared by the Up next list and the settings sheet. */
export function useCalendar() {
  const [data, setData] = useState<CalendarCalls | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(() => api.get<CalendarCalls>('/meetings/calendar').then((r) => setData(r.data)).catch(() => {}), []);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const run = async (label: string, fn: () => Promise<{ data: CalendarCalls }>, fallback: string) => {
    setBusy(label);
    setError('');
    try {
      const res = await fn();
      setData(res.data);
      return res.data;
    } catch (e) {
      setError(errorMessage(e, fallback));
      return null;
    } finally {
      setBusy('');
    }
  };
  const answer = (call: CalendarCall, action: CallAction) =>
    run(`${call.id}:${action}`, () => api.post<CalendarCalls>(`/meetings/calendar/calls/${call.id}`, { action }), tr('ui.upcomingCalls.couldNotSaveYourAnswer'));
  const save = (changes: Partial<CalendarSettings>) =>
    run('settings', () => api.put<CalendarCalls>('/meetings/calendar', changes), tr('ui.upcomingCalls.couldNotSaveTheSettings'));
  const check = () => run('check', () => api.post<CalendarCalls>('/meetings/calendar/check'), tr('ui.upcomingCalls.couldNotReadYourCalendar'));

  return { data, busy, error, answer, save, check };
}

export type Calendar = ReturnType<typeof useCalendar>;

/** Calls from the person's calendar in the next 24 hours, a line each, with
 *  Send and Skip right there. */
export function UpNext({ calendar, agents, highlight, onOpenMeeting, onOpenSettings }: {
  calendar: Calendar;
  agents: { id: string; name: string }[];
  highlight?: string | null;
  onOpenMeeting: (meetingId: string) => void;
  onOpenSettings: () => void;
}) {
  const { data, busy, error } = calendar;
  const [all, setAll] = useState(false);

  useEffect(() => {
    if (!highlight) return;
    setAll(true);
    window.setTimeout(() => document.getElementById(`call-${highlight}`)?.scrollIntoView({ block: 'center' }), 100);
  }, [highlight, data?.calls.length]);

  if (!data) return null;
  const { settings, calls } = data;
  const agentName = agents.find((a) => a.id === settings?.agent_id)?.name;
  const shown = all ? calls : calls.slice(0, 3);

  const answer = async (call: CalendarCall, action: CallAction) => {
    const next = await calendar.answer(call, action);
    const sent = next?.calls.find((c) => c.id === call.id);
    if (sent?.meeting_id && action === 'send') onOpenMeeting(sent.meeting_id);
  };

  return (
    <section aria-labelledby="up-next" className="flex flex-col gap-1.5 rounded-[10px] border border-line bg-panel px-3 py-2.5">
      <div className="flex items-center gap-2">
        <CalendarDays size={15} aria-hidden className="flex-none text-fg-dim" />
        <h2 id="up-next" className="text-[13px] font-medium">{tr('ui.upcomingCalls.upNext')}</h2>
        <span className="flex-1 truncate text-[12px] text-fg-dim">{tr('ui.upcomingCalls.fromYourCalendar')}</span>
        {data.connected && settings?.enabled && (
          <button type="button" title={tr('ui.upcomingCalls.checkNow')} aria-label={tr('ui.upcomingCalls.checkNow')} disabled={!!busy}
            onClick={() => void calendar.check()}
            className="flex h-8 w-8 items-center justify-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg disabled:opacity-50 md:h-7 md:w-7">
            <RefreshCw size={13} aria-hidden className={cn(busy === 'check' && 'animate-spin')} />
          </button>
        )}
      </div>
      {!data.connected ? (
        <p className="text-[12px] leading-relaxed text-fg-dim">
          {tr('ui.upcomingCalls.seeYourMeetTeamsAnd')}{' '}
          <Link to="/settings/apps" className="text-accent-ink hover:underline">{tr('ui.upcomingCalls.connectGoogleCalendar')}</Link>
        </p>
      ) : !settings?.enabled ? (
        <p className="text-[12px] text-fg-dim">
          {tr('ui.upcomingCalls.callSuggestionsAreOff')}{' '}
          <button type="button" onClick={() => void calendar.save({ enabled: true })} className="text-accent-ink hover:underline">{tr('ui.upcomingCalls.turnOn')}</button>
        </p>
      ) : calls.length === 0 ? (
        <p className="text-[12px] text-fg-dim">{tr('ui.upcomingCalls.noCallsWithAMeeting')}</p>
      ) : (
        <ul className="flex flex-col">
          {shown.map((call) => {
            const pending = busy.startsWith(`${call.id}:`);
            const inactive = call.status === 'missed' || call.status === 'cancelled';
            const canSend = call.status === 'suggested' || call.status === 'skipped' || call.status === 'failed';
            const more = (call.series && call.status === 'suggested') || (call.decided_by === 'series' && (call.status === 'approved' || call.status === 'skipped'));
            return (
              <li key={call.id} id={`call-${call.id}`}
                className={cn('flex flex-col gap-1 border-t border-line py-2 first:border-t-0', highlight === call.id && '-mx-1.5 rounded-md bg-accent/10 px-1.5', inactive && 'opacity-60')}>
                <div className="flex min-w-0 items-baseline gap-2">
                  <span className="flex-none whitespace-nowrap font-mono text-[11px] text-fg-dim">{callTime(call.starts_at)}</span>
                  <span className="min-w-0 flex-1 truncate text-[13px] font-medium" title={call.title}>{call.title}</span>
                </div>
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className={cn('min-w-0 flex-1 truncate text-[11.5px]', call.status === 'failed' ? 'text-danger-ink' : call.status === 'approved' || call.status === 'joined' ? 'text-accent-ink' : 'text-fg-dim')}
                    title={call.error || undefined}>
                    {call.status === 'suggested' ? PLATFORM[call.platform] ?? call.platform : statusLabel(call)}
                  </span>
                  {canSend && (
                    <Button type="button" size="sm" variant={call.status === 'suggested' ? 'primary' : 'outline'} disabled={pending}
                      className="h-9 px-2.5 text-[12px] md:h-7" onClick={() => void answer(call, 'send')}>
                      {call.status === 'failed' ? tr('ui.upcomingCalls.tryAgain') : agentName ? tr('ui.upcomingCalls.sendAgentname', { agentName }) : tr('ui.upcomingCalls.sendAgent')}
                    </Button>
                  )}
                  {(call.status === 'suggested' || call.status === 'approved') && (
                    <Button type="button" size="sm" variant="ghost" disabled={pending} className="h-9 px-2 text-[12px] md:h-7"
                      onClick={() => void answer(call, 'skip')}>
                      {call.status === 'approved' ? tr('ui.upcomingCalls.donTSend') : tr('ui.upcomingCalls.skip')}
                    </Button>
                  )}
                  {call.meeting_id && (
                    <Button type="button" size="sm" variant="ghost" className="h-9 px-2 text-[12px] md:h-7" onClick={() => onOpenMeeting(call.meeting_id)}>{tr('ui.upcomingCalls.open')}</Button>
                  )}
                  {more && (
                    <Menu label={tr('ui.upcomingCalls.moreForTitle', { title: call.title })} trigger={({ open, toggle }) => (
                      <button type="button" aria-label={tr('ui.upcomingCalls.more')} aria-expanded={open} onClick={toggle} disabled={pending}
                        className="flex h-9 w-8 items-center justify-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg md:h-7 md:w-7">
                        <MoreHorizontal size={15} aria-hidden />
                      </button>
                    )}>
                      {(close) => call.status === 'suggested' ? (
                        <>
                          <MenuItem title={tr('ui.upcomingCalls.alwaysForThisSeries')} onSelect={() => { close(); void answer(call, 'always'); }} />
                          <MenuItem title={tr('ui.upcomingCalls.neverForThisSeries')} onSelect={() => { close(); void answer(call, 'never'); }} />
                        </>
                      ) : (
                        <MenuItem title={tr('ui.upcomingCalls.askMeAgain')} onSelect={() => { close(); void answer(call, 'ask'); }} />
                      )}
                    </Menu>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {calls.length > 3 && (
        <button type="button" onClick={() => setAll((v) => !v)} className="self-start text-[12px] text-fg-dim hover:text-fg hover:underline">
          {all ? tr('ui.upcomingCalls.showFewer') : tr('ui.upcomingCalls.valueMore', { value: calls.length - 3 })}
        </button>
      )}
      {settings?.error && <p role="alert" className="text-[12px] text-danger-ink">{settings.error}</p>}
      {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
      {data.connected && settings?.enabled && (
        <button type="button" onClick={onOpenSettings} className="self-start text-[11.5px] text-fg-dim hover:text-fg hover:underline">
          {tr('ui.upcomingCalls.rules')}
        </button>
      )}
    </section>
  );
}

/** The calendar part of the settings sheet: suggestions on or off and the rules. */
export function CalendarRulesFields({ calendar, agents, rules, onChange }: {
  calendar: Calendar;
  agents: { id: string; name: string }[];
  rules: CalendarRules;
  onChange: (rules: CalendarRules) => void;
}) {
  const data = calendar.data;
  if (!data) return null;
  if (!data.connected) {
    return (
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        {tr('ui.upcomingCalls.connectGoogleCalendarAndYour')}{' '}
        <Link to="/settings/apps" className="text-accent-ink hover:underline">{tr('ui.upcomingCalls.connectItInSettingsConnected')}</Link>
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <label className="flex items-start gap-3">
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-medium">{tr('ui.upcomingCalls.suggestCalls')}</span>
          <span className="block text-[12px] leading-relaxed text-fg-dim">{tr('ui.upcomingCalls.yourNext24HoursRead', { remind_minutes: rules.remind_minutes })}</span>
        </span>
        <input type="checkbox" role="switch" checked={rules.enabled} onChange={(e) => onChange({ ...rules, enabled: e.target.checked })}
          className="mt-1 h-4 w-4 flex-none accent-[var(--accent)]" />
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
          {tr('ui.upcomingCalls.agentThatJoins')}
          <Select value={rules.agent_id} onChange={(e) => onChange({ ...rules, agent_id: e.target.value })} className="h-9">
            <option value="">{tr('ui.upcomingCalls.yourMainAgent')}</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
          {tr('ui.upcomingCalls.withoutAsking')}
          <Select value={rules.auto_join} onChange={(e) => onChange({ ...rules, auto_join: e.target.value as CalendarSettings['auto_join'] })} className="h-9">
            <option value="ask">{tr('ui.upcomingCalls.askMeAboutEveryCall')}</option>
            <option value="mine">{tr('ui.upcomingCalls.joinTheCallsIOrganise')}</option>
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-fg-dim sm:col-span-2">
          {tr('ui.upcomingCalls.neverSuggestCallsWhoseTitle')}
          <textarea value={rules.never_words} onChange={(e) => onChange({ ...rules, never_words: e.target.value })} rows={2} maxLength={1000}
            placeholder={tr('ui.upcomingCalls.n11Interview')} className="rounded-[8px] border border-line bg-bg px-2.5 py-2 text-[13px] text-fg" />
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
          {tr('ui.upcomingCalls.remindMeThisManyMinutes')}
          <Input type="number" min={1} max={120} value={rules.remind_minutes}
            onChange={(e) => onChange({ ...rules, remind_minutes: Number(e.target.value) || 15 })} className="h-9" />
        </label>
      </div>
      {data.settings.series_choices > 0 && (
        <p className="text-[12px] text-fg-dim">{tr('ui.upcomingCalls.youChoseAlwaysOrNever', { series_choices: data.settings.series_choices })}</p>
      )}
    </div>
  );
}
