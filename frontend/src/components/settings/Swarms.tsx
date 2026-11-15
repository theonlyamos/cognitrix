import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Group, SettingRow, primaryButtonClass } from '@/components/settings/ui';
import { tokensLabel, type SwarmLimits } from '@/lib/swarms';
import { tr } from '@/lib/i18n';

const inputClass =
  'h-10 w-28 rounded-lg border border-line bg-panel-2 px-3 text-right font-mono text-[13px] outline-none focus:border-accent md:h-9';

type Field = 'concurrency' | 'max_workers' | 'token_budget' | 'auto_start';

/** Limits on swarms: how many workers an agent may start and what they may spend. */
export function SwarmSettings() {
  const [limits, setLimits] = useState<SwarmLimits | null>(null);
  const [draft, setDraft] = useState<Record<Field, string>>({ concurrency: '', max_workers: '', token_budget: '', auto_start: '' });
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const adopt = (data: SwarmLimits) => {
    setLimits(data);
    setDraft({
      concurrency: String(data.concurrency),
      max_workers: String(data.max_workers),
      token_budget: String(data.token_budget),
      auto_start: String(data.auto_start),
    });
  };

  useEffect(() => {
    api.get<SwarmLimits>('/swarms/settings')
      .then(({ data }) => adopt(data))
      .catch((err) => setError(errorMessage(err, tr('ui.settings.swarms.couldNotLoadTheSwarm'))));
  }, []);

  const save = async (patch: Record<string, unknown>) => {
    setBusy(true);
    setError('');
    setSaved(false);
    try {
      const { data } = await api.put<SwarmLimits>('/swarms/settings', patch);
      adopt(data);
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err, tr('ui.settings.swarms.couldNotSaveTheSwarm')));
    } finally {
      setBusy(false);
    }
  };

  if (!limits) return <p role={error ? 'alert' : 'status'} className="text-[13px] text-fg-dim">{error || tr('ui.settings.swarms.loading')}</p>;

  const number = (field: Field, label: string, max?: number) => (
    <input
      type="number"
      inputMode="numeric"
      min={field === 'auto_start' ? 0 : 1}
      max={max}
      aria-label={label}
      value={draft[field]}
      disabled={!limits.enabled}
      onChange={(e) => { setSaved(false); setDraft((d) => ({ ...d, [field]: e.target.value })); }}
      className={inputClass}
    />
  );
  const changed = (Object.keys(draft) as Field[]).some((field) => draft[field] !== String(limits[field]));

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save(Object.fromEntries((Object.keys(draft) as Field[]).map((field) => [field, Number(draft[field])])));
      }}
    >
      <Group>
        <SettingRow
          label={tr('ui.settings.swarms.letAgentsStartSwarms')}
          detail={tr('ui.settings.swarms.anAgentCanSplitA')}
        >
          <label className="flex min-h-11 cursor-pointer items-center gap-2 text-[13px] md:min-h-0">
            <input
              type="checkbox"
              checked={limits.enabled}
              disabled={busy}
              onChange={(e) => void save({ enabled: e.target.checked })}
              className="accent-[var(--accent)]"
            />
            {limits.enabled ? tr('ui.settings.swarms.on') : tr('ui.settings.swarms.off')}
          </label>
        </SettingRow>
        <SettingRow label={tr('ui.settings.swarms.workersAtOnce')} detail={tr('ui.settings.swarms.howManyOfASwarm', { concurrency: limits.ceilings.concurrency })}>
          {number('concurrency', 'Workers at once', limits.ceilings.concurrency)}
        </SettingRow>
        <SettingRow label={tr('ui.settings.swarms.workersPerSwarm')} detail={tr('ui.settings.swarms.theMostWorkersOneSwarm', { max_workers: limits.ceilings.max_workers })}>
          {number('max_workers', 'Workers per swarm', limits.ceilings.max_workers)}
        </SettingRow>
        <SettingRow
          label={tr('ui.settings.swarms.startWithoutAsking')}
          detail={tr('ui.settings.swarms.anAgentMayStartThis')}
        >
          {number('auto_start', tr('ui.settings.swarms.startWithoutAsking'))}
        </SettingRow>
        <SettingRow
          label={tr('ui.settings.swarms.tokenLimitPerSwarm')}
          detail={tr('ui.settings.swarms.aSwarmStopsStartingWorkers', { kensLabel: tokensLabel(limits.token_budget), kensLabel2: tokensLabel(limits.ceilings.token_budget) })}
        >
          {number('token_budget', 'Token limit per swarm', limits.ceilings.token_budget)}
        </SettingRow>
      </Group>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="submit" disabled={busy || !changed || !limits.enabled} className={primaryButtonClass}>{tr('ui.settings.swarms.saveLimits')}</button>
        {saved && <span role="status" className="text-[12.5px] text-fg-dim">{tr('ui.settings.swarms.saved')}</span>}
        {error && <span role="alert" className="text-[12.5px] text-danger-ink">{error}</span>}
      </div>
    </form>
  );
}
