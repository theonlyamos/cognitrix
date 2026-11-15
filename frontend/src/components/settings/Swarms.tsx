import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Group, SettingRow, primaryButtonClass } from '@/components/settings/ui';
import { tokensLabel, type SwarmLimits } from '@/lib/swarms';

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
      .catch((err) => setError(errorMessage(err, 'Could not load the swarm limits.')));
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
      setError(errorMessage(err, 'Could not save the swarm limits.'));
    } finally {
      setBusy(false);
    }
  };

  if (!limits) return <p role={error ? 'alert' : 'status'} className="text-[13px] text-fg-dim">{error || 'Loading…'}</p>;

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
          label="Let agents start swarms"
          detail="An agent can split a big job across workers that run at the same time and report back together. Turn it off and agents work one step at a time."
        >
          <label className="flex min-h-11 cursor-pointer items-center gap-2 text-[13px] md:min-h-0">
            <input
              type="checkbox"
              checked={limits.enabled}
              disabled={busy}
              onChange={(e) => void save({ enabled: e.target.checked })}
              className="accent-[var(--accent)]"
            />
            {limits.enabled ? 'On' : 'Off'}
          </label>
        </SettingRow>
        <SettingRow label="Workers at once" detail={`How many of a swarm's workers run at the same time. Up to ${limits.ceilings.concurrency} on this server.`}>
          {number('concurrency', 'Workers at once', limits.ceilings.concurrency)}
        </SettingRow>
        <SettingRow label="Workers per swarm" detail={`The most workers one swarm may have. Up to ${limits.ceilings.max_workers}.`}>
          {number('max_workers', 'Workers per swarm', limits.ceilings.max_workers)}
        </SettingRow>
        <SettingRow
          label="Start without asking"
          detail="An agent may start this many workers on its own. Above that it asks you first, unless you sent the message with Swarm switched on. 0 means it always asks."
        >
          {number('auto_start', 'Start without asking')}
        </SettingRow>
        <SettingRow
          label="Token limit per swarm"
          detail={`A swarm stops starting workers once its workers have used this many tokens. Now ${tokensLabel(limits.token_budget)}; up to ${tokensLabel(limits.ceilings.token_budget)}.`}
        >
          {number('token_budget', 'Token limit per swarm', limits.ceilings.token_budget)}
        </SettingRow>
      </Group>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="submit" disabled={busy || !changed || !limits.enabled} className={primaryButtonClass}>Save limits</button>
        {saved && <span role="status" className="text-[12.5px] text-fg-dim">Saved.</span>}
        {error && <span role="alert" className="text-[12.5px] text-danger-ink">{error}</span>}
      </div>
    </form>
  );
}
