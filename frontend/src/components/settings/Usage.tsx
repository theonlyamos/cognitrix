import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Group, Segmented, SubHeading } from '@/components/settings/ui';
import { CHATGPT_USAGE_URL } from '@/lib/chatgptPlan';

interface Totals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cache_write_tokens: number;
  cost: number;
  unpriced_tokens: number;
}
interface UsageSummary {
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
      <ul className="flex h-28 items-end gap-px" aria-label={byCost ? 'Cost per day' : 'Tokens per day'}>
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
            {formatTokens(row.totals.prompt_tokens)} in · {formatTokens(row.totals.completion_tokens)} out
            {row.totals.cached_tokens > 0 && ` · ${Math.round((row.totals.cached_tokens / row.totals.prompt_tokens) * 100)}% cached`}
          </div>
          {showCost && (
            <div className="w-[92px] text-right text-[13px] font-medium tabular-nums">
              {row.totals.unpriced_tokens > 0 && row.totals.cost === 0 ? 'no price' : formatCost(row.totals.cost)}
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
      .catch((err) => { if (live) setError(errorMessage(err, 'Could not load usage.')); });
    return () => { live = false; };
  }, [range]);

  const showCost = !!data?.prices_available;
  const total = data?.total;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Segmented<Range>
          label="Period"
          value={range}
          onChange={setRange}
          options={[{ value: '7', label: '7 days' }, { value: '30', label: '30 days' }, { value: '90', label: '90 days' }]}
        />
      </div>
      {error && <p role="alert" className="mb-3 text-[13px] text-danger-ink">{error}</p>}
      {!data && !error && <p className="text-[13px] text-fg-dim">Loading…</p>}
      {data && total && (
        <>
          <Group className="flex flex-wrap divide-y-0">
            {showCost && <Stat label="ESTIMATED COST" value={formatCost(total.cost)} />}
            <Stat label="TOKENS IN" value={formatTokens(total.prompt_tokens)} detail={total.cached_tokens > 0 ? `${Math.round((total.cached_tokens / total.prompt_tokens) * 100)}% read from cache` : undefined} />
            <Stat label="TOKENS OUT" value={formatTokens(total.completion_tokens)} />
            <Stat label="MODEL CALLS" value={total.calls.toLocaleString()} />
          </Group>
          {total.calls === 0 ? (
            <p className="mt-4 text-[13px] text-fg-dim">
              Nothing yet for this period. Usage is counted from the chats and tasks your agents run from now on.
            </p>
          ) : (
            <>
              <SubHeading>{showCost ? 'COST PER DAY' : 'TOKENS PER DAY'}</SubHeading>
              <DayBars days={data.by_day} byCost={showCost} />
              <SubHeading>BY MODEL</SubHeading>
              <Breakdown
                showCost={showCost}
                rows={data.by_model.map((m) => ({ key: `${m.provider}/${m.model}`, name: m.model || 'Unknown model', sub: m.provider, totals: m }))}
              />
              <SubHeading>BY AGENT</SubHeading>
              <Breakdown
                showCost={showCost}
                rows={data.by_agent.map((a) => ({ key: a.agent_id || 'other', name: a.agent_name, totals: a }))}
              />
            </>
          )}
          <p className="mt-4 max-w-[62ch] text-[12px] leading-relaxed text-fg-dim">
            {showCost
              ? 'Costs are estimates from public list prices, so your provider’s bill is the final word.'
              : 'Costs are not shown because model prices could not be loaded.'}
            {showCost && total.unpriced_tokens > 0 && ` ${formatTokens(total.unpriced_tokens)} tokens went to models with no known price and are not in the cost.`}
            {data?.by_model.some((m) => m.provider === 'chatgpt') && (
              <>
                {' '}ChatGPT plan use is counted at no cost here because it comes out of your plan.{' '}
                <a href={CHATGPT_USAGE_URL} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Manage usage</a>
              </>
            )}
          </p>
        </>
      )}
    </div>
  );
}
