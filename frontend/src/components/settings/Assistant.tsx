import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import { enableProactive, getProactivity, saveProactivity, type Proactivity, type ProactivityLevel } from '@/lib/proactive';
import { Group, Segmented, SettingRow, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';

const timeClass = 'h-10 rounded-lg border border-line bg-panel-2 px-2 font-mono text-[13px] outline-none focus:border-accent md:h-9';
const numberClass = 'h-10 w-28 rounded-lg border border-line bg-panel-2 px-3 text-right font-mono text-[13px] outline-none focus:border-accent md:h-9';

function levelDetail(level: ProactivityLevel): string {
  switch (level) {
    case 'off': return tr('ui.settings.assistant.yourAssistantOnlyDoesWhat');
    case 'brief': return tr('ui.settings.assistant.oneMorningBriefADay');
    case 'eager': return tr('ui.settings.assistant.checkInsTheBriefMeeting');
    default: return tr('ui.settings.assistant.checkInsTheMorningBrief');
  }
}

function tokensText(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

/** Settings › Assistant: how proactive your main assistant is, and when it may interrupt you. */
export function AssistantSettings() {
  const [data, setData] = useState<Proactivity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({ quiet_start: '', quiet_end: '', brief_time: '', max_pushes: '', daily_tokens: '' });

  const adopt = (d: Proactivity) => {
    setData(d);
    setDraft({
      quiet_start: d.quiet_start, quiet_end: d.quiet_end, brief_time: d.brief_time,
      max_pushes: String(d.max_pushes), daily_tokens: String(d.daily_tokens),
    });
  };

  useEffect(() => { getProactivity().then(adopt).catch((e) => setError(errorMessage(e, tr('ui.settings.assistant.couldNotLoadTheAssistant')))); }, []);

  const act = async (fn: () => Promise<Proactivity>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      adopt(await fn());
    } catch (e) {
      setError(errorMessage(e, fallback));
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <p role={error ? 'alert' : 'status'} className="text-[13px] text-fg-dim">{error || tr('ui.settings.assistant.loading')}</p>;

  const changed = draft.quiet_start !== data.quiet_start || draft.quiet_end !== data.quiet_end || draft.brief_time !== data.brief_time
    || draft.max_pushes !== String(data.max_pushes) || draft.daily_tokens !== String(data.daily_tokens);
  const off = data.level === 'off';
  const zone = data.timezone || data.browser_timezone || 'UTC';

  return (
    <div className="space-y-2">
      {data.needs_setup && (
        <div role="status" className="flex flex-wrap items-center gap-3 rounded-[12px] border border-accent/40 bg-accent/10 px-3.5 py-3">
          <p className="min-w-0 flex-1 basis-[240px] text-[13px]">
            {data.main_assistant
              ? tr('ui.settings.assistant.nameIsnTCheckingIn', { name: data.main_assistant.name })
              : tr('ui.settings.assistant.createAnAssistantFirstThen')}
          </p>
          {data.main_assistant && (
            <button type="button" className={primaryButtonClass} disabled={busy}
              onClick={() => void act(enableProactive, tr('ui.settings.assistant.couldNotTurnCheckIns'))}>
              {tr('ui.settings.assistant.turnOnCheckIns')}
            </button>
          )}
        </div>
      )}

      <Group>
        <SettingRow label={tr('ui.settings.assistant.howProactive')} detail={levelDetail(data.level)}>
          <Segmented
            label={tr('ui.settings.assistant.howProactive')}
            value={data.level}
            options={[
              { value: 'off', label: tr('ui.settings.assistant.off') },
              { value: 'brief', label: tr('ui.settings.assistant.briefOnly') },
              { value: 'balanced', label: tr('ui.settings.assistant.balanced') },
              { value: 'eager', label: tr('ui.settings.assistant.eager') },
            ]}
            onChange={(level) => void act(() => saveProactivity({ level }), tr('ui.settings.assistant.couldNotChangeTheSetting'))}
          />
        </SettingRow>
        {data.main_assistant && (
          <SettingRow
            label={tr('ui.settings.assistant.mainAssistant')}
            detail={data.main_assistant.checkins
              ? tr('ui.settings.assistant.checksInEveryValueHours', { value: Math.round(data.main_assistant.every_minutes / 60 * 10) / 10 })
              : tr('ui.settings.assistant.checkInsAreOff')}
          >
            <Link to={`/agents/${encodeURIComponent(data.main_assistant.id)}`} className={buttonClass}>{data.main_assistant.name}</Link>
          </SettingRow>
        )}
      </Group>

      <form
        className={off ? 'opacity-60' : undefined}
        onSubmit={(e) => {
          e.preventDefault();
          void act(() => saveProactivity({
            quiet_start: draft.quiet_start, quiet_end: draft.quiet_end, brief_time: draft.brief_time,
            max_pushes: Number(draft.max_pushes), daily_tokens: Number(draft.daily_tokens),
          }), tr('ui.settings.assistant.couldNotSaveTheSettings'));
        }}
      >
        <SubHeading>{tr('ui.settings.assistant.whenItMayInterruptYou')}</SubHeading>
        <Group>
          <SettingRow label={tr('ui.settings.assistant.morningBrief')} detail={tr('ui.settings.assistant.readyAtThisTimeEach', { zone })}>
            <input type="time" aria-label={tr('ui.settings.assistant.morningBriefTime')} value={draft.brief_time} disabled={off}
              onChange={(e) => setDraft((d) => ({ ...d, brief_time: e.target.value }))} className={timeClass} />
          </SettingRow>
          <SettingRow label={tr('ui.settings.assistant.quietHours')} detail={tr('ui.settings.assistant.noPushesToYourPhone')}>
            <input type="time" aria-label={tr('ui.settings.assistant.quietHoursStart')} value={draft.quiet_start} disabled={off}
              onChange={(e) => setDraft((d) => ({ ...d, quiet_start: e.target.value }))} className={timeClass} />
            <span className="text-[12.5px] text-fg-dim">{tr('ui.settings.assistant.to')}</span>
            <input type="time" aria-label={tr('ui.settings.assistant.quietHoursEnd')} value={draft.quiet_end} disabled={off}
              onChange={(e) => setDraft((d) => ({ ...d, quiet_end: e.target.value }))} className={timeClass} />
          </SettingRow>
          <SettingRow label={tr('ui.settings.assistant.alertsADay')} detail={tr('ui.settings.assistant.howManyTimesADay')}>
            <input type="number" inputMode="numeric" min={0} max={data.limits.max_pushes} aria-label={tr('ui.settings.assistant.alertsADay')} value={draft.max_pushes}
              disabled={off} onChange={(e) => setDraft((d) => ({ ...d, max_pushes: e.target.value }))} className={numberClass} />
          </SettingRow>
          <SettingRow label={tr('ui.settings.assistant.dailyTokenLimit')} detail={tr('ui.settings.assistant.forWorkYouDidnT')}>
            <input type="number" inputMode="numeric" min={0} max={data.limits.daily_tokens} step={10000} aria-label={tr('ui.settings.assistant.dailyTokenLimit')}
              value={draft.daily_tokens} disabled={off}
              onChange={(e) => setDraft((d) => ({ ...d, daily_tokens: e.target.value }))} className={numberClass} />
          </SettingRow>
        </Group>
        {changed && (
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" className={quietButtonClass} onClick={() => adopt(data)}>{tr('ui.settings.assistant.cancel')}</button>
            <button type="submit" className={primaryButtonClass} disabled={busy}>{tr('ui.settings.assistant.save')}</button>
          </div>
        )}
      </form>

      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}

      <SubHeading>{tr('ui.settings.assistant.thisWeek')}</SubHeading>
      <Group>
        <SettingRow label={tr('ui.settings.assistant.tokensUsed')}
          detail={`${tokensText(data.today_tokens)} today${data.daily_tokens ? ` of ${tokensText(data.daily_tokens)}` : ''}. See what that costs in Settings › Usage.`}>
          <span className="font-mono text-[13px]">{tokensText(data.week.tokens)}</span>
        </SettingRow>
        <SettingRow label={tr('ui.settings.assistant.checkIns')}
          detail={tr('ui.settings.assistant.skippedSkippedBecauseNothingHad', { skipped: data.week.skipped, value: data.backoff > 1 ? ` Checking in ${data.backoff}x less often after several "not useful" answers.` : '' })}>
          <span className="font-mono text-[13px]">{data.week.runs}</span>
        </SettingRow>
        <SettingRow label={tr('ui.settings.assistant.alerts')} detail={tr('ui.settings.assistant.heldHeldBackByQuiet', { held: data.week.held })}>
          <span className="font-mono text-[13px]">{data.week.pushes}</span>
        </SettingRow>
      </Group>

      {data.muted.length > 0 && (
        <>
          <SubHeading>{tr('ui.settings.assistant.neverBringUp')}</SubHeading>
          <ul className="divide-y divide-line rounded-[12px] border border-line bg-panel">
            {data.muted.map((topic) => (
              <li key={topic} className="flex items-center gap-3 px-3.5 py-2.5">
                <span className="min-w-0 flex-1 text-[13px]">{topic}</span>
                <button type="button" className={quietButtonClass} disabled={busy}
                  onClick={() => void act(() => saveProactivity({ muted: data.muted.filter((t) => t !== topic) }), tr('ui.settings.assistant.couldNotChangeTheSetting'))}>
                  {tr('ui.settings.assistant.allowAgain')}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
