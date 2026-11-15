import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Group, Segmented, SubHeading } from '@/components/settings/ui';
import { CHATGPT_USAGE_URL } from '@/lib/chatgptPlan';
import { tr, dateLocale } from '@/lib/i18n';

interface Totals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cache_write_tokens: number;
  cost: number;
  unpriced_tokens: number;
}
export interface CapStatus {
  cap: { source: 'own' | 'default' | 'none'; mode: string; monthly_cost: number; monthly_tokens: number };
  used: { cost: number; tokens: number; unpriced_tokens: number; calls: number };
  fraction: number;
  reached: boolean;
  month: string;
  resets_on: string;
}
interface UsageSummary {
  spending_cap?: CapStatus | null;
  days: number;
  prices_available: boolean;
  total: Totals;
  by_day: (Totals & { day: string })[];
  by_model: (Totals & { provider: string; model: string })[];
  by_agent: (Totals & { agent_id: string; agent_name: string })[];
}

type Range = '7' | '30' | '90';

export const formatTokens = (n: number) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(n);
};

export const formatCost = (n: number) => {
  if (n === 0) return '$0.00';
  if (n < 0.01) return 'under $0.01';
  return `$${n.toFixed(2)}`;
};

const tokensOf = (t: Totals) => t.prompt_tokens + t.completion_tokens;

/** "$4.10 of $20.00" / "1.2M of 5M tokens" lines for a monthly cap. */
export function capLines(state: CapStatus): string[] {
  const lines: string[] = [];
  if (state.cap.monthly_cost) lines.push(tr('ui.settings.usage.costOfMonthlyCost', { cost: formatCost(state.used.cost), monthly_cost: state.cap.monthly_cost.toFixed(2) }));
  if (state.cap.monthly_tokens) lines.push(tr('ui.settings.usage.tokensOfTokens2Tokens', { tokens: formatTokens(state.used.tokens), tokens2: formatTokens(state.cap.monthly_tokens) }));
  return lines;
}

export function CapMeter({ state }: { state: CapStatus }) {
  const percent = Math.min(100, Math.round(state.fraction * 100));
  const tone = state.reached ? 'bg-danger' : state.fraction >= 0.8 ? 'bg-warn' : 'bg-accent';
  return (
    <div
      className="h-1.5 overflow-hidden rounded-full bg-panel-2"
      role="meter"
      aria-label={tr('ui.settings.usage.shareOfThisMonthS')}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
    >
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.max(percent, state.fraction > 0 ? 2 : 0)}%` }} />
    </div>
  );
}

function CapCard({ state }: { state: CapStatus }) {
  const percent = Math.round(state.fraction * 100);
  return (
    <div className="mb-4 rounded-[12px] border border-line bg-panel p-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{tr('ui.settings.usage.thisMonthSLimit')}</div>
        <div className="text-[12px] text-fg-dim">{tr('ui.settings.usage.resetsLocaledatestring', { localeDateString: new Date(`${state.resets_on}T00:00:00Z`).toLocaleDateString(dateLocale()) })}</div>
      </div>
      <div className="mt-1 text-[15px] font-semibold tabular-nums">{capLines(state).join(' · ')}</div>
      <div className="mt-2"><CapMeter state={state} /></div>
      <p className={`mt-2 text-[12px] leading-relaxed ${state.reached ? 'text-danger-ink' : 'text-fg-dim'}`}>
        {state.reached
          ? tr('ui.settings.usage.youHaveReachedYourLimit')
          : tr('ui.settings.usage.percentUsedChatsTasksAnd', { percent, value: state.cap.source === 'default' ? 'This is the limit an admin set for everyone.' : 'An admin set this limit for you.' })}
      </p>
    </div>
  );
}

function Stat({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0 flex-1 basis-[130px] px-3.5 py-3">
      <div className="font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{label}</div>
      <div className="mt-1 text-[19px] font-semibold tabular-nums">{value}</div>
      {detail && <div className="mt-0.5 text-[11.5px] text-fg-dim">{detail}</div>}
    </div>
  );
}

function DayBars({ days, byCost }: { days: UsageSummary['by_day']; byCost: boolean }) {
  const amount = (d: Totals) => (byCost ? d.cost : tokensOf(d));
  const peak = Math.max(...days.map(amount), 0);
  const label = (d: Totals & { day: string }) =>
    `${d.day}: ${byCost ? formatCost(d.cost) : `${formatTokens(tokensOf(d))} tokens`}`;
  return (
    <div className="rounded-[12px] border border-line bg-panel p-3.5">
      <ul className="flex h-28 items-end gap-px" aria-label={byCost ? tr('ui.settings.usage.costPerDay') : tr('ui.settings.usage.tokensPerDay')}>
        {days.map((d) => (
          <li key={d.day} className="group relative flex h-full min-w-0 flex-1 items-end" title={label(d)}>
            <span className="sr-only">{label(d)}</span>
            <span
              aria-hidden="true"
              className="w-full rounded-t-[2px] bg-accent/70 group-hover:bg-accent"
              style={{ height: peak > 0 && amount(d) > 0 ? `${Math.max(3, (amount(d) / peak) * 100)}%` : '1px' }}
            />
          </li>
        ))}
      </ul>
      <div className="mt-2 flex justify-between font-mono text-[10.5px] text-fg-dim">
        <span>{days[0]?.day}</span>
        <span>{days[days.length - 1]?.day}</span>
      </div>
    </div>
  );
}

function Breakdown({ rows, showCost }: { rows: { key: string; name: string; sub?: string; totals: Totals }[]; showCost: boolean }) {
  return (
    <Group>
      {rows.map((row) => (
        <div key={row.key} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3.5 py-2.5">
          <div className="min-w-0 flex-1 basis-[160px]">
            <div className="truncate text-[13.5px] font-medium">{row.name}</div>
            {row.sub && <div className="font-mono text-[10.5px] text-fg-dim">{row.sub}</div>}
          </div>
          <div className="text-right text-[12px] tabular-nums text-fg-dim">
            {formatTokens(row.totals.prompt_tokens)}{' '}{tr('ui.settings.usage.in')}{' '}{formatTokens(row.totals.completion_tokens)}{' '}{tr('ui.settings.usage.out')}
            {row.totals.cached_tokens > 0 && tr('ui.settings.usage.roundCached', { round: Math.round((row.totals.cached_tokens / row.totals.prompt_tokens) * 100) })}
          </div>
          {showCost && (
            <div className="w-[92px] text-right text-[13px] font-medium tabular-nums">
              {row.totals.unpriced_tokens > 0 && row.totals.cost === 0 ? tr('ui.settings.usage.noPrice') : formatCost(row.totals.cost)}
            </div>
          )}
        </div>
      ))}
    </Group>
  );
}

export function UsageSettings() {
  const [range, setRange] = useState<Range>('30');
  const [data, setData] = useState<UsageSummary | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    setError('');
    api.get<UsageSummary>('/usage', { params: { days: Number(range) } })
      .then((res) => { if (live) setData(res.data); })
      .catch((err) => { if (live) setError(errorMessage(err, tr('ui.settings.usage.couldNotLoadUsage'))); });
    return () => { live = false; };
  }, [range]);

  const showCost = !!data?.prices_available;
  const total = data?.total;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Segmented<Range>
          label={tr('ui.settings.usage.period')}
          value={range}
          onChange={setRange}
          options={[{ value: '7', label: tr('ui.settings.usage.n7Days') }, { value: '30', label: tr('ui.settings.usage.n30Days') }, { value: '90', label: tr('ui.settings.usage.n90Days') }]}
        />
      </div>
      {error && <p role="alert" className="mb-3 text-[13px] text-danger-ink">{error}</p>}
      {!data && !error && <p className="text-[13px] text-fg-dim">{tr('ui.settings.usage.loading')}</p>}
      {data && total && (
        <>
          {data.spending_cap && data.spending_cap.cap.source !== 'none' && <CapCard state={data.spending_cap} />}
          <Group className="flex flex-wrap divide-y-0">
            {showCost && <Stat label={tr('ui.settings.usage.estimatedCost')} value={formatCost(total.cost)} />}
            <Stat label={tr('ui.settings.usage.tokensIn')} value={formatTokens(total.prompt_tokens)} detail={total.cached_tokens > 0 ? tr('ui.settings.usage.roundReadFromCache', { round: Math.round((total.cached_tokens / total.prompt_tokens) * 100) }) : undefined} />
            <Stat label={tr('ui.settings.usage.tokensOut')} value={formatTokens(total.completion_tokens)} />
            <Stat label={tr('ui.settings.usage.modelCalls')} value={total.calls.toLocaleString(dateLocale())} />
          </Group>
          {total.calls === 0 ? (
            <p className="mt-4 text-[13px] text-fg-dim">
              {tr('ui.settings.usage.nothingYetForThisPeriod')}
            </p>
          ) : (
            <>
              <SubHeading>{showCost ? tr('ui.settings.usage.costPerDay_64c3') : tr('ui.settings.usage.tokensPerDay_3811')}</SubHeading>
              <DayBars days={data.by_day} byCost={showCost} />
              <SubHeading>{tr('ui.settings.usage.byModel')}</SubHeading>
              <Breakdown
                showCost={showCost}
                rows={data.by_model.map((m) => ({ key: `${m.provider}/${m.model}`, name: m.model || 'Unknown model', sub: m.provider, totals: m }))}
              />
              <SubHeading>{tr('ui.settings.usage.byAgent')}</SubHeading>
              <Breakdown
                showCost={showCost}
                rows={data.by_agent.map((a) => ({ key: a.agent_id || 'other', name: a.agent_name, totals: a }))}
              />
            </>
          )}
          <p className="mt-4 max-w-[62ch] text-[12px] leading-relaxed text-fg-dim">
            {showCost
              ? tr('ui.settings.usage.costsAreEstimatesFromPublic')
              : tr('ui.settings.usage.costsAreNotShownBecause')}
            {showCost && total.unpriced_tokens > 0 && tr('ui.settings.usage.tokensTokensWentToModels', { tokens: formatTokens(total.unpriced_tokens) })}
            {data?.by_model.some((m) => m.provider === 'chatgpt') && (
              <>
                {' '}{tr('ui.settings.usage.chatgptPlanUseIsCounted')}{' '}
                <a href={CHATGPT_USAGE_URL} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{tr('ui.settings.usage.manageUsage')}</a>
              </>
            )}
          </p>
        </>
      )}
    </div>
  );
}
