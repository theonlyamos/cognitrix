import { useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Group, Segmented } from '@/components/settings/ui';
import { CHATGPT_USAGE_URL } from '@/lib/chatgptPlan';
import { tr, dateLocale } from '@/lib/i18n';
import { cn } from '@/lib/utils';

interface Totals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cache_write_tokens: number;
  cost: number;
  unpriced_tokens: number;
  /** What the same use would cost at API list prices, plan use included. */
  api_cost?: number;
  api_unpriced_tokens?: number;
  cache_saved?: number;
}
/** One model's or agent's share of a day. */
interface Part {
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cost: number;
  api_cost?: number;
}
type Day = Totals & { day: string; models?: Record<string, Part>; agents?: Record<string, Part> };
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
  by_day: Day[];
  by_model: (Totals & { provider: string; model: string })[];
  by_agent: (Totals & { agent_id: string; agent_name: string })[];
}

type Range = '7' | '30' | '90';
type Metric = 'tokens' | 'cost';
type Split = 'type' | 'model' | 'agent';

export const formatTokens = (n: number) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(Math.round(n));
};

export const formatCost = (n: number) => {
  if (n === 0) return '$0.00';
  if (n < 0.01) return 'under $0.01';
  return `$${n.toFixed(2)}`;
};

const tokensOf = (t: { prompt_tokens: number; completion_tokens: number }) => t.prompt_tokens + t.completion_tokens;
const apiCost = (t: { cost: number; api_cost?: number }) => t.api_cost ?? t.cost;
const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);
const percent = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0);

const COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)'];
const SOFT = 'var(--chart-1-soft)';
const MAX_SERIES = 5;

function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return v;
}

function dayLabel(day: string) {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(dateLocale(), { month: 'short', day: 'numeric', timeZone: 'UTC' });
}

function readChoice<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const saved = localStorage.getItem(key) as T | null;
    return saved && allowed.includes(saved) ? saved : fallback;
  } catch {
    return fallback;
  }
}

function saveChoice(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private window: the choice just isn't remembered */
  }
}

/** "$4.10 of $20.00" / "1.2M of 5M tokens" lines for a monthly cap. */
export function capLines(state: CapStatus): string[] {
  const lines: string[] = [];
  if (state.cap.monthly_cost) lines.push(tr('ui.settings.usage.costOfMonthlyCost', { cost: formatCost(state.used.cost), monthly_cost: state.cap.monthly_cost.toFixed(2) }));
  if (state.cap.monthly_tokens) lines.push(tr('ui.settings.usage.tokensOfTokens2Tokens', { tokens: formatTokens(state.used.tokens), tokens2: formatTokens(state.cap.monthly_tokens) }));
  return lines;
}

export function CapMeter({ state }: { state: CapStatus }) {
  const value = Math.min(100, Math.round(state.fraction * 100));
  const tone = state.reached ? 'bg-danger' : state.fraction >= 0.8 ? 'bg-warn' : 'bg-accent';
  return (
    <div
      className="h-1.5 overflow-hidden rounded-full bg-panel-2"
      role="meter"
      aria-label={tr('ui.settings.usage.shareOfThisMonthS')}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
    >
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.max(value, state.fraction > 0 ? 2 : 0)}%` }} />
    </div>
  );
}

function CapCard({ state }: { state: CapStatus }) {
  const used = Math.round(state.fraction * 100);
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
          : tr('ui.settings.usage.percentUsedChatsTasksAnd', { percent: used, value: state.cap.source === 'default' ? 'This is the limit an admin set for everyone.' : 'An admin set this limit for you.' })}
      </p>
    </div>
  );
}

function Sparkline({ values, color }: { values: number[]; color: string }) {
  const w = 72;
  const h = 26;
  const peak = Math.max(...values, 0);
  if (values.length < 2 || peak <= 0) return null;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 2 - (v / peak) * (h - 4)] as const);
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-[26px] w-[72px] flex-none overflow-visible" aria-hidden="true">
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="2.2" fill={color} />
    </svg>
  );
}

function Tile({ label, value, detail, spark, color }: { label: string; value: string; detail?: string; spark: number[]; color: string }) {
  return (
    <div className="min-w-0 rounded-[12px] border border-line bg-panel px-3.5 py-3">
      <div className="truncate font-mono text-[10.5px] uppercase tracking-[0.12em] text-fg-dim">{label}</div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <div className="text-[19px] font-semibold tabular-nums">{value}</div>
        <Sparkline values={spark} color={color} />
      </div>
      {detail && <div className="mt-0.5 text-[11.5px] leading-snug text-fg-dim">{detail}</div>}
    </div>
  );
}

interface Series {
  key: string;
  name: string;
  color: string;
  /** Per day: input tokens (tokens view) or dollars (cost view). */
  values: number[];
  /** Per day output tokens, drawn below the line. Tokens view only. */
  out?: number[];
}

function Bars({ series, days, down, peak, onHover }: { series: Series[]; days: number; down?: boolean; peak: number; onHover: (i: number | null) => void }) {
  return (
    <ul className={cn('flex gap-px', down ? 'h-12 items-start' : 'h-40 items-end')} aria-hidden="true">
      {Array.from({ length: days }, (_, i) => {
        const total = sum(series.map((s) => (down ? s.out?.[i] ?? 0 : s.values[i])));
        return (
          <li
            key={i}
            className={cn('flex h-full min-w-0 flex-1', down ? 'flex-col' : 'flex-col-reverse')}
            onMouseEnter={() => onHover(i)}
          >
            {total <= 0 ? (
              <span className="h-px w-full bg-line" />
            ) : total / peak < 0.02 ? (
              // Too small to split: one thin mark, so quiet days stay quiet.
              <span className="h-[2px] w-full opacity-70" style={{ background: series[0]?.color }} />
            ) : (
              series.map((s) => {
                const v = down ? s.out?.[i] ?? 0 : s.values[i];
                if (v <= 0) return null;
                return <span key={s.key} className="w-full" style={{ height: `${(v / peak) * 100}%`, background: s.color }} />;
              })
            )}
          </li>
        );
      })}
    </ul>
  );
}

function DayChart({ data, metric, split, names }: { data: UsageSummary; metric: Metric; split: Split; names: { models: Series[]; agents: Series[] } }) {
  const [hover, setHover] = useState<number | null>(null);
  const days = data.by_day;
  const n = days.length;
  const tokens = metric === 'tokens';
  const fmt = tokens ? formatTokens : formatCost;

  let series: Series[];
  if (split === 'type') {
    series = tokens
      ? [
          { key: 'new', name: tr('ui.settings.usage.newInput'), color: COLORS[0], values: days.map((d) => Math.max(0, d.prompt_tokens - d.cached_tokens)), out: days.map((d) => d.completion_tokens) },
          { key: 'cached', name: tr('ui.settings.usage.readFromCache'), color: SOFT, values: days.map((d) => d.cached_tokens) },
        ]
      : [
          { key: 'paid', name: tr('ui.settings.usage.paid'), color: COLORS[3], values: days.map((d) => d.cost) },
          { key: 'covered', name: tr('ui.settings.usage.coveredByPlans'), color: COLORS[0], values: days.map((d) => Math.max(0, apiCost(d) - d.cost)) },
        ];
  } else {
    series = split === 'model' ? names.models : names.agents;
  }
  const outSeries = tokens ? (split === 'type' ? [{ ...series[0], color: COLORS[1] }] : series) : [];
  const dayTotals = days.map((_, i) => sum(series.map((s) => s.values[i])));
  const outTotals = days.map((_, i) => sum(outSeries.map((s) => s.out?.[i] ?? 0)));
  const top = niceMax(Math.max(...dayTotals, 0));
  const bottom = niceMax(Math.max(...outTotals, 0));
  const all = sum(dayTotals);
  const paidLine = !tokens && split !== 'type';

  const tip = hover === null ? null : (
    <div
      className="pointer-events-none absolute top-2 z-10 min-w-[170px] rounded-lg border border-line bg-panel-2 px-2.5 py-2 text-[11.5px] shadow-panel"
      style={hover > n / 2 ? { right: `${((n - hover - 0.5) / n) * 100}%`, marginRight: 10 } : { left: `${((hover + 0.5) / n) * 100}%`, marginLeft: 10 }}
    >
      <div className="mb-1 font-mono text-[10.5px] text-fg-dim">{dayLabel(days[hover].day)}</div>
      {[...series].reverse().map((s) => (
        <div key={s.key} className="flex justify-between gap-3 tabular-nums">
          <span className="truncate"><i className="mr-1.5 inline-block h-2 w-2 rounded-[2px]" style={{ background: s.color }} />{s.name}</span>
          <b className="font-medium">{fmt(s.values[hover])}</b>
        </div>
      ))}
      {tokens && (
        <div className="flex justify-between gap-3 tabular-nums">
          <span>{tr('ui.settings.usage.output')}</span>
          <b className="font-medium">{formatTokens(outTotals[hover])}</b>
        </div>
      )}
      {paidLine && (
        <div className="mt-1 flex justify-between gap-3 border-t border-line pt-1 tabular-nums">
          <span>{tr('ui.settings.usage.paid')}</span>
          <b className="font-medium">{formatCost(days[hover].cost)}</b>
        </div>
      )}
    </div>
  );

  return (
    <div className="relative" onMouseLeave={() => setHover(null)}>
      <ul className="sr-only" aria-label={tokens ? tr('ui.settings.usage.tokensPerDay') : tr('ui.settings.usage.costPerDay')}>
        {days.map((d, i) => (
          <li key={d.day}>{`${d.day}: ${fmt(dayTotals[i])}${tokens ? `, ${formatTokens(outTotals[i])} out` : ''}`}</li>
        ))}
      </ul>
      <div className="mb-1 text-right font-mono text-[10px] text-fg-dim">{tokens ? tr('ui.settings.usage.topIn', { top: formatTokens(top) }) : formatCost(top)}</div>
      <div className="relative">
        <div className="pointer-events-none absolute inset-x-0 top-1/2 border-t border-dashed border-line" />
        <Bars series={series} days={n} peak={top} onHover={setHover} />
        {paidLine && (
          <ul className="pointer-events-none absolute inset-0 flex items-end gap-px" aria-hidden="true">
            {days.map((d) => (
              <li key={d.day} className="flex h-full min-w-0 flex-1 flex-col-reverse">
                <span className="w-full" style={{ height: `${(d.cost / top) * 100}%` }} />
                <span className="h-[2px] w-full bg-ok" />
              </li>
            ))}
          </ul>
        )}
      </div>
      {tokens && (
        <>
          <div className="my-1 h-px bg-fg-dim/50" />
          <div className="relative">
            <Bars series={outSeries} days={n} peak={bottom} down onHover={setHover} />
            <div className="pointer-events-none absolute bottom-0 left-0 font-mono text-[10px] text-fg-dim">{tr('ui.settings.usage.bottomOut', { bottom: formatTokens(bottom) })}</div>
          </div>
        </>
      )}
      <div className="mt-2 flex justify-between font-mono text-[10.5px] text-fg-dim">
        <span>{dayLabel(days[0].day)}</span>
        {n > 2 && <span>{dayLabel(days[Math.floor(n / 2)].day)}</span>}
        <span>{dayLabel(days[n - 1].day)}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[12px] text-fg-dim">
        {paidLine && (
          <span><i className="mr-1.5 inline-block h-[3px] w-2.5 align-middle bg-ok" />{tr('ui.settings.usage.paid')} <b className="font-medium text-fg tabular-nums">{formatCost(data.total.cost)}</b></span>
        )}
        {series.map((s) => (
          <span key={s.key}>
            <i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-[2px] align-[-1px]" style={{ background: s.color }} />
            {s.name} <b className="font-medium text-fg tabular-nums">{fmt(sum(s.values))}</b>
            {tokens && split !== 'type' && <> {tr('ui.settings.usage.in')} <b className="font-medium text-fg tabular-nums">{formatTokens(sum(s.out ?? []))}</b> {tr('ui.settings.usage.out')}</>}
            {' · '}{percent(sum(s.values), all)}%
          </span>
        ))}
        {tokens && split === 'type' && (
          <span><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-[2px] align-[-1px]" style={{ background: COLORS[1] }} />{tr('ui.settings.usage.outputBelowTheLine')} <b className="font-medium text-fg tabular-nums">{formatTokens(data.total.completion_tokens)}</b></span>
        )}
      </div>
      {tip}
    </div>
  );
}

function ShareBars({ rows, metric }: { rows: { key: string; name: string; sub?: string; color: string; value: number; unpriced: boolean }[]; metric: Metric }) {
  const peak = Math.max(...rows.map((r) => r.value), 0);
  const all = sum(rows.map((r) => r.value));
  return (
    <ul className="mt-1 space-y-2.5">
      {rows.map((r) => (
        <li key={r.key}>
          <div className="flex items-baseline justify-between gap-2 text-[13px]">
            <span className="min-w-0 truncate font-medium">
              {r.name}
              {r.sub && <span className="ml-1.5 font-mono text-[10.5px] font-normal text-fg-dim">{r.sub}</span>}
            </span>
            <span className="flex-none text-[12.5px] tabular-nums">
              {r.unpriced ? <span className="text-fg-dim">{tr('ui.settings.usage.noPrice')}</span> : metric === 'tokens' ? formatTokens(r.value) : formatCost(r.value)}
              <span className="ml-1.5 text-fg-dim">{percent(r.value, all)}%</span>
            </span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-panel-2">
            <div className="h-full rounded-full" style={{ width: `${peak > 0 ? Math.max((r.value / peak) * 100, r.value > 0 ? 1.5 : 0) : 0}%`, background: r.color }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function CacheRing({ share }: { share: number }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 96 96" className="h-24 w-24" aria-hidden="true">
      <circle cx="48" cy="48" r={r} fill="none" stroke="rgb(var(--panel-2-rgb))" strokeWidth="10" />
      {share > 0 && (
        <circle cx="48" cy="48" r={r} fill="none" stroke="var(--chart-3)" strokeWidth="10" strokeLinecap="round" strokeDasharray={`${c * share} ${c}`} transform="rotate(-90 48 48)" />
      )}
      <text x="48" y="54" textAnchor="middle" className="fill-fg text-[17px] font-semibold">{Math.round(share * 100)}%</text>
    </svg>
  );
}

/** Group a list into the biggest few plus "Other", so the chart keeps 5 colours. */
function topWithOther<T>(items: T[], size: (t: T) => number): { shown: T[]; rest: T[] } {
  const sorted = [...items].sort((a, b) => size(b) - size(a));
  if (sorted.length <= MAX_SERIES) return { shown: sorted, rest: [] };
  return { shown: sorted.slice(0, MAX_SERIES - 1), rest: sorted.slice(MAX_SERIES - 1) };
}

export function UsageSettings() {
  const [range, setRange] = useState<Range>('30');
  const [metric, setMetricState] = useState<Metric>(() => readChoice('usage.metric', ['tokens', 'cost'] as const, 'tokens'));
  const [split, setSplitState] = useState<Split>(() => readChoice('usage.split', ['type', 'model', 'agent'] as const, 'model'));
  const [data, setData] = useState<UsageSummary | null>(null);
  const [error, setError] = useState('');

  const setMetric = (m: Metric) => { setMetricState(m); saveChoice('usage.metric', m); };
  const setSplit = (s: Split) => { setSplitState(s); saveChoice('usage.split', s); };

  useEffect(() => {
    let live = true;
    setError('');
    api.get<UsageSummary>('/usage', { params: { days: Number(range) } })
      .then((res) => { if (live) setData(res.data); })
      .catch((err) => { if (live) setError(errorMessage(err, tr('ui.settings.usage.couldNotLoadUsage'))); });
    return () => { live = false; };
  }, [range]);

  const pricesOn = !!data?.prices_available;
  const shown: Metric = pricesOn ? metric : 'tokens';
  const tokens = shown === 'tokens';

  // Model and agent series for the day chart, in the order of the panels below.
  const names = useMemo(() => {
    if (!data) return { models: [] as Series[], agents: [] as Series[] };
    const size = (t: Totals) => (tokens ? tokensOf(t) : apiCost(t));
    const build = <T,>(items: T[], key: (t: T) => string, name: (t: T) => string, totals: (t: T) => Totals, pick: (d: Day) => Record<string, Part> | undefined): Series[] => {
      const { shown: top, rest } = topWithOther(items, (t) => size(totals(t)));
      const value = (p: Part | undefined) => (p ? (tokens ? p.prompt_tokens : apiCost(p)) : 0);
      const series: Series[] = top.map((t) => ({
        key: key(t),
        name: name(t),
        color: COLORS[Math.min(items.indexOf(t), MAX_SERIES - 1)],
        values: data.by_day.map((d) => value(pick(d)?.[key(t)])),
        out: data.by_day.map((d) => pick(d)?.[key(t)]?.completion_tokens ?? 0),
      }));
      if (rest.length) {
        const keys = rest.map(key);
        series.push({
          key: '__other',
          name: tr('ui.settings.usage.other'),
          color: COLORS[MAX_SERIES - 1],
          values: data.by_day.map((d) => sum(keys.map((k) => value(pick(d)?.[k])))),
          out: data.by_day.map((d) => sum(keys.map((k) => pick(d)?.[k]?.completion_tokens ?? 0))),
        });
      }
      return series;
    };
    return {
      models: build(data.by_model, (m) => `${m.provider}/${m.model}`, (m) => m.model || 'Unknown model', (m) => m, (d) => d.models),
      agents: build(data.by_agent, (a) => a.agent_id, (a) => a.agent_name, (a) => a, (d) => d.agents),
    };
  }, [data, tokens]);

  const total = data?.total;
  const days = data?.by_day ?? [];

  const panelRows = <T extends Totals>(items: T[], key: (t: T) => string, name: (t: T) => string, sub?: (t: T) => string | undefined) => {
    const size = (t: T) => (tokens ? tokensOf(t) : apiCost(t));
    const sorted = [...items].sort((a, b) => size(b) - size(a));
    return sorted.map((t) => ({
      key: key(t),
      name: name(t),
      sub: sub?.(t),
      // Same colour as in the day chart, whichever view is showing.
      color: COLORS[Math.min(items.indexOf(t), MAX_SERIES - 1)],
      value: size(t),
      unpriced: !tokens && apiCost(t) === 0 && (t.api_unpriced_tokens ?? t.unpriced_tokens) > 0,
    }));
  };

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
          {total.calls === 0 ? (
            <Group className="flex flex-wrap divide-y-0">
              <p className="px-3.5 py-3 text-[13px] text-fg-dim">{tr('ui.settings.usage.nothingYetForThisPeriod')}</p>
            </Group>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
                {tokens ? (
                  <>
                    <Tile label={tr('ui.settings.usage.tokensIn')} value={formatTokens(total.prompt_tokens)} detail={tr('ui.settings.usage.roundReadFromCache', { round: percent(total.cached_tokens, total.prompt_tokens) })} spark={days.map((d) => d.prompt_tokens)} color={COLORS[0]} />
                    <Tile label={tr('ui.settings.usage.tokensOut')} value={formatTokens(total.completion_tokens)} spark={days.map((d) => d.completion_tokens)} color={COLORS[1]} />
                    <Tile label={tr('ui.settings.usage.cacheReads')} value={`${percent(total.cached_tokens, total.prompt_tokens)}%`} detail={tr('ui.settings.usage.ofInput')} spark={days.map((d) => (d.prompt_tokens ? d.cached_tokens / d.prompt_tokens : 0))} color={COLORS[2]} />
                    <Tile label={tr('ui.settings.usage.modelCalls')} value={total.calls.toLocaleString(dateLocale())} detail={tr('ui.settings.usage.perDay', { count: Math.round(total.calls / Math.max(days.length, 1)).toLocaleString(dateLocale()) })} spark={days.map((d) => d.calls)} color={COLORS[3]} />
                  </>
                ) : (
                  <>
                    <Tile label={tr('ui.settings.usage.paid')} value={formatCost(total.cost)} detail={tr('ui.settings.usage.apiKeysOnly')} spark={days.map((d) => d.cost)} color={COLORS[3]} />
                    <Tile label={tr('ui.settings.usage.atApiPrices')} value={formatCost(apiCost(total))} detail={tr('ui.settings.usage.coveredByPlansAmount', { amount: formatCost(Math.max(0, apiCost(total) - total.cost)) })} spark={days.map((d) => apiCost(d))} color={COLORS[0]} />
                    <Tile label={tr('ui.settings.usage.savedByCache')} value={formatCost(total.cache_saved ?? 0)} detail={tr('ui.settings.usage.atApiPrices_lower')} spark={days.map((d) => d.cache_saved ?? 0)} color={COLORS[2]} />
                    <Tile label={tr('ui.settings.usage.per1mTokens')} value={formatCost(tokensOf(total) ? (apiCost(total) / tokensOf(total)) * 1e6 : 0)} detail={tr('ui.settings.usage.atApiPrices_lower')} spark={days.map((d) => (tokensOf(d) ? apiCost(d) / tokensOf(d) : 0))} color={COLORS[1]} />
                  </>
                )}
              </div>

              <div className="mt-3 rounded-[12px] border border-line bg-panel p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-fg-dim">
                      {tokens ? tr('ui.settings.usage.tokensPerDay') : tr('ui.settings.usage.costPerDayAtApiPrices')}
                    </div>
                    <div className="mt-1 flex flex-wrap items-baseline gap-x-2">
                      <span className="text-[24px] font-semibold tabular-nums">{tokens ? formatTokens(tokensOf(total)) : formatCost(apiCost(total))}</span>
                      <span className="text-[12.5px] text-fg-dim">
                        {tokens
                          ? tr('ui.settings.usage.inOut', { input: formatTokens(total.prompt_tokens), output: formatTokens(total.completion_tokens) })
                          : tr('ui.settings.usage.youPaidCost', { cost: formatCost(total.cost) })}
                      </span>
                    </div>
                  </div>
                  {pricesOn && (
                    <Segmented<Metric>
                      label={tr('ui.settings.usage.show')}
                      value={shown}
                      onChange={setMetric}
                      options={[{ value: 'tokens', label: tr('ui.settings.usage.tokens') }, { value: 'cost', label: tr('ui.settings.usage.cost') }]}
                    />
                  )}
                </div>
                <div className="mb-4 mt-3 flex flex-wrap items-center gap-2">
                  <span className="text-[12px] text-fg-dim">{tr('ui.settings.usage.splitBy')}</span>
                  <Segmented<Split>
                    label={tr('ui.settings.usage.splitBy')}
                    value={split}
                    onChange={setSplit}
                    options={[{ value: 'type', label: tr('ui.settings.usage.type') }, { value: 'model', label: tr('ui.settings.usage.model') }, { value: 'agent', label: tr('ui.settings.usage.agent') }]}
                  />
                </div>
                <DayChart data={data} metric={shown} split={split} names={names} />
              </div>

              <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[1fr_1fr_0.8fr]">
                <section className="min-w-0 rounded-[12px] border border-line bg-panel p-3.5" aria-label={tr('ui.settings.usage.byModel')}>
                  <div className="mb-1 font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{tr('ui.settings.usage.byModel')}</div>
                  <ShareBars
                    metric={shown}
                    rows={panelRows(data.by_model, (m) => `${m.provider}/${m.model}`, (m) => m.model || 'Unknown model', (m) => m.provider)}
                  />
                </section>
                <section className="min-w-0 rounded-[12px] border border-line bg-panel p-3.5" aria-label={tr('ui.settings.usage.byAgent')}>
                  <div className="mb-1 font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{tr('ui.settings.usage.byAgent')}</div>
                  <ShareBars metric={shown} rows={panelRows(data.by_agent, (a) => a.agent_id || 'other', (a) => a.agent_name)} />
                </section>
                <section className="flex min-w-0 flex-col rounded-[12px] border border-line bg-panel p-3.5" aria-label={tr('ui.settings.usage.cache')}>
                  <div className="font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{tr('ui.settings.usage.cache_upper')}</div>
                  <div className="my-3 flex justify-center"><CacheRing share={total.prompt_tokens ? total.cached_tokens / total.prompt_tokens : 0} /></div>
                  <p className="text-center text-[11.5px] leading-relaxed text-fg-dim">
                    {tr('ui.settings.usage.cachedOfInput', { cached: formatTokens(total.cached_tokens), input: formatTokens(total.prompt_tokens) })}
                    {(() => {
                      if (!tokens) return (total.cache_saved ?? 0) > 0 ? <><br />{tr('ui.settings.usage.savedAboutAtApi', { amount: formatCost(total.cache_saved ?? 0) })}</> : null;
                      const best = [...data.by_model].filter((m) => m.prompt_tokens >= total.prompt_tokens * 0.02).sort((a, b) => b.cached_tokens / (b.prompt_tokens || 1) - a.cached_tokens / (a.prompt_tokens || 1))[0];
                      return best && best.cached_tokens > 0 ? <><br />{tr('ui.settings.usage.bestCache', { model: best.model, round: percent(best.cached_tokens, best.prompt_tokens) })}</> : null;
                    })()}
                  </p>
                </section>
              </div>
            </>
          )}
          <p className="mt-4 max-w-[62ch] text-[12px] leading-relaxed text-fg-dim">
            {pricesOn ? tr('ui.settings.usage.costsAreEstimatesPlanUse') : tr('ui.settings.usage.costsAreNotShownBecause')}
            {pricesOn && (total.api_unpriced_tokens ?? total.unpriced_tokens) > 0 && tr('ui.settings.usage.tokensTokensWentToModels', { tokens: formatTokens(total.api_unpriced_tokens ?? total.unpriced_tokens) })}
            {data.by_model.some((m) => m.provider === 'chatgpt') && (
              <>
                {' '}
                <a href={CHATGPT_USAGE_URL} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{tr('ui.settings.usage.manageChatgptUsage')}</a>
              </>
            )}
          </p>
        </>
      )}
    </div>
  );
}
