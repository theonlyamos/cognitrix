import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
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

const PLATFORM: Record<string, string> = { meet: 'Google Meet', teams: 'Teams', zoom: 'Zoom' };

/** "Mon 16 Nov, 09:30" in the person's own time zone (the server stores UTC). */
function callTime(stamp: string): string {
  const date = new Date(`${stamp.replace(' ', 'T')}Z`);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(dateLocale(), { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
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

export function UpcomingCalls({ agents, highlight, onOpenMeeting }: {
  agents: { id: string; name: string }[];
  highlight?: string | null;
  onOpenMeeting: (meetingId: string) => void;
}) {
  const [data, setData] = useState<CalendarCalls | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [rulesOpen, setRulesOpen] = useState(false);
  const [rules, setRules] = useState<Pick<CalendarSettings, 'agent_id' | 'auto_join' | 'never_words' | 'remind_minutes'> | null>(null);

  const load = useCallback(() => api.get<CalendarCalls>('/meetings/calendar').then((r) => setData(r.data)).catch(() => {}), []);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!highlight) return;
    window.setTimeout(() => document.getElementById(`call-${highlight}`)?.scrollIntoView({ block: 'center' }), 100);
  }, [highlight, data?.calls.length]);

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

  const answer = async (call: CalendarCall, action: CallAction) => {
    const next = await run(`${call.id}:${action}`, () => api.post<CalendarCalls>(`/meetings/calendar/calls/${call.id}`, { action }), tr('ui.upcomingCalls.couldNotSaveYourAnswer'));
    const sent = next?.calls.find((c) => c.id === call.id);
    if (sent?.meeting_id) onOpenMeeting(sent.meeting_id);
  };

  const save = (changes: Partial<CalendarSettings>) =>
    run('settings', () => api.put<CalendarCalls>('/meetings/calendar', changes), tr('ui.upcomingCalls.couldNotSaveTheSettings'));

  if (!data) return null;
  const { settings, calls } = data;

  if (!data.connected) {
    return (
      <p className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[12.5px] leading-relaxed text-fg-dim">
        {tr('ui.upcomingCalls.connectGoogleCalendarAndYour')}{' '}
        <Link to="/settings/apps" className="text-accent-ink hover:underline">{tr('ui.upcomingCalls.connectItInSettingsConnected')}</Link>
      </p>
    );
  }

  const openRules = () => {
    setRules({ agent_id: settings.agent_id, auto_join: settings.auto_join, never_words: settings.never_words, remind_minutes: settings.remind_minutes });
    setRulesOpen(!rulesOpen);
  };

  return (
    <section aria-labelledby="upcoming-calls" className="flex flex-col gap-2 rounded-[10px] border border-line bg-panel px-4 py-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="upcoming-calls" className="mr-auto text-[14px] font-medium">{tr('ui.upcomingCalls.upcomingCallsFromYourCalendar')}</h2>
        <label className="flex items-center gap-2 text-[12.5px]">
          <input type="checkbox" checked={settings.enabled} disabled={busy === 'settings'}
            onChange={(e) => void save({ enabled: e.target.checked })} className="h-4 w-4 accent-[var(--accent)]" />
          {tr('ui.upcomingCalls.suggestCalls')}
        </label>
        <Button type="button" size="sm" variant="ghost" disabled={!!busy || !settings.enabled}
          onClick={() => void run('check', () => api.post<CalendarCalls>('/meetings/calendar/check'), tr('ui.upcomingCalls.couldNotReadYourCalendar'))}>
          {busy === 'check' ? tr('ui.upcomingCalls.checking') : tr('ui.upcomingCalls.checkNow')}
        </Button>
        <Button type="button" size="sm" variant="ghost" aria-expanded={rulesOpen} onClick={openRules}>{tr('ui.upcomingCalls.rules')}</Button>
      </div>
      <p className="text-[12px] leading-relaxed text-fg-dim">
        {tr('ui.upcomingCalls.yourNext24HoursRead', { remind_minutes: settings.remind_minutes })}
      </p>
      {settings.error && <p role="alert" className="text-[12.5px] text-danger-ink">{settings.error}</p>}
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}

      {rulesOpen && rules && (
        <form className="grid gap-3 rounded-[8px] border border-line px-3 py-3 sm:grid-cols-2"
          onSubmit={(e) => { e.preventDefault(); void save(rules).then((r) => { if (r) setRulesOpen(false); }); }}>
          <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
            {tr('ui.upcomingCalls.agentThatJoins')}
            <Select value={rules.agent_id} onChange={(e) => setRules({ ...rules, agent_id: e.target.value })} className="h-9">
              <option value="">{tr('ui.upcomingCalls.yourMainAgent')}</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          </label>
          <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
            {tr('ui.upcomingCalls.withoutAsking')}
            <Select value={rules.auto_join} onChange={(e) => setRules({ ...rules, auto_join: e.target.value as CalendarSettings['auto_join'] })} className="h-9">
              <option value="ask">{tr('ui.upcomingCalls.askMeAboutEveryCall')}</option>
              <option value="mine">{tr('ui.upcomingCalls.joinTheCallsIOrganise')}</option>
            </Select>
          </label>
          <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim sm:col-span-2">
            {tr('ui.upcomingCalls.neverSuggestCallsWhoseTitle')}
            <textarea value={rules.never_words} onChange={(e) => setRules({ ...rules, never_words: e.target.value })} rows={3} maxLength={1000}
              placeholder={tr('ui.upcomingCalls.n11Interview')} className="rounded-[8px] border border-line bg-bg px-2.5 py-2 text-[13px] text-fg" />
          </label>
          <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
            {tr('ui.upcomingCalls.remindMeThisManyMinutes')}
            <Input type="number" min={1} max={120} value={rules.remind_minutes}
              onChange={(e) => setRules({ ...rules, remind_minutes: Number(e.target.value) || 15 })} className="h-9" />
          </label>
          <div className="flex items-end gap-2">
            <Button type="submit" size="sm" disabled={busy === 'settings'}>{tr('ui.upcomingCalls.saveRules')}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setRulesOpen(false)}>{tr('ui.upcomingCalls.cancel')}</Button>
          </div>
          {settings.series_choices > 0 && (
            <p className="text-[12px] text-fg-dim sm:col-span-2">
              {tr('ui.upcomingCalls.youChoseAlwaysOrNever', { series_choices: settings.series_choices })}
            </p>
          )}
        </form>
      )}

      {settings.enabled && calls.length === 0 && (
        <p className="py-1 text-[12.5px] text-fg-dim">{tr('ui.upcomingCalls.noCallsWithAMeeting')}</p>
      )}
      {calls.length > 0 && (
        <ul className="divide-y divide-line">
          {calls.map((call) => {
            const pending = busy.startsWith(`${call.id}:`);
            const inactive = call.status === 'missed' || call.status === 'cancelled';
            return (
              <li key={call.id} id={`call-${call.id}`}
                className={cn('flex flex-col gap-1.5 py-2.5', highlight === call.id && 'rounded-[6px] bg-accent/10 px-2', inactive && 'opacity-60')}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-mono text-[12px] text-fg-dim">{callTime(call.starts_at)}</span>
                  <span className="text-[13.5px] font-medium">{call.title}</span>
                  <span className="text-[12px] text-fg-dim">
                    {PLATFORM[call.platform] ?? call.platform}{call.organizer && !call.organised_by_me ? ` · ${call.organizer}` : ''}{call.organised_by_me ? tr('ui.upcomingCalls.youOrganiseIt') : ''}
                  </span>
                  <span className={cn('ml-auto text-[12px]', call.status === 'failed' ? 'text-danger-ink' : call.status === 'approved' || call.status === 'joined' ? 'text-accent-ink' : 'text-fg-dim')}>
                    {statusLabel(call)}
                  </span>
                </div>
                {call.status === 'failed' && call.error && <p className="text-[12px] text-danger-ink">{call.error}</p>}
                <div className="flex flex-wrap gap-1.5">
                  {(call.status === 'suggested' || call.status === 'skipped' || call.status === 'failed') && (
                    <Button type="button" size="sm" disabled={pending} onClick={() => void answer(call, 'send')}>
                      {call.status === 'failed' ? tr('ui.upcomingCalls.tryAgain') : tr('ui.upcomingCalls.sendAgent')}
                    </Button>
                  )}
                  {(call.status === 'suggested' || call.status === 'approved') && (
                    <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={() => void answer(call, 'skip')}>
                      {call.status === 'approved' ? tr('ui.upcomingCalls.donTSend') : tr('ui.upcomingCalls.skip')}
                    </Button>
                  )}
                  {call.series && call.status === 'suggested' && (
                    <>
                      <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={() => void answer(call, 'always')}>{tr('ui.upcomingCalls.alwaysForThisSeries')}</Button>
                      <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={() => void answer(call, 'never')}>{tr('ui.upcomingCalls.neverForThisSeries')}</Button>
                    </>
                  )}
                  {call.decided_by === 'series' && (call.status === 'approved' || call.status === 'skipped') && (
                    <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={() => void answer(call, 'ask')}>{tr('ui.upcomingCalls.askMeAgain')}</Button>
                  )}
                  {call.meeting_id && (
                    <Button type="button" size="sm" variant="outline" onClick={() => onOpenMeeting(call.meeting_id)}>{tr('ui.upcomingCalls.openTheCall')}</Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
