import type { CardBlock } from '@/lib/cards';

type Chart = Extract<CardBlock, { type: 'chart' }>;

// Series colours: the accent first, then quieter tones that read in both themes.
const SERIES = ['rgb(var(--accent-ink-rgb))', 'rgb(var(--fg-dim-rgb))', 'rgb(var(--ok-rgb))', 'rgb(var(--danger-ink-rgb))'];

const fmt = (n: number): string => {
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 10_000) return `${(n / 1000).toFixed(abs >= 100_000 ? 0 : 1)}k`;
  return Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
};

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * pow >= value) ?? 10;
  return step * pow;
}

function Pie({ chart }: { chart: Chart }) {
  const values = chart.series[0].values.map((v) => Math.max(0, v));
  const total = values.reduce((a, b) => a + b, 0) || 1;
  let angle = -Math.PI / 2;
  const slices = values.map((v, i) => {
    const start = angle;
    angle += (v / total) * Math.PI * 2;
    const large = angle - start > Math.PI ? 1 : 0;
    const p = (a: number) => `${60 + 52 * Math.cos(a)} ${60 + 52 * Math.sin(a)}`;
    const d = values.length === 1 || v === total
      ? 'M 60 8 A 52 52 0 1 1 59.99 8 Z'
      : `M 60 60 L ${p(start)} A 52 52 0 ${large} 1 ${p(angle)} Z`;
    return <path key={i} d={d} fill={i === 0 ? 'rgb(var(--accent-rgb))' : `rgb(var(--fg-dim-rgb) / ${Math.max(0.15, 0.75 - i * 0.12)})`} stroke="rgb(var(--panel-rgb))" strokeWidth="1.5" />;
  });
  return (
    <div className="flex flex-wrap items-center gap-5">
      <svg viewBox="0 0 120 120" width="120" height="120" role="img" aria-label={chart.title || 'Pie chart'}>{slices}</svg>
      <ul className="space-y-1 text-[12.5px]">
        {chart.labels.map((label, i) => (
          <li key={i} className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 flex-none rounded-sm" style={{ background: i === 0 ? 'rgb(var(--accent-rgb))' : `rgb(var(--fg-dim-rgb) / ${Math.max(0.15, 0.75 - i * 0.12)})` }} />
            <span>{label}</span>
            <span className="font-mono text-[11px] text-fg-dim">{fmt(values[i])}{chart.unit ? ` ${chart.unit}` : ''} · {Math.round((values[i] / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Bar, line, area and pie charts drawn as SVG, sized to the card. */
export function CardChart({ chart }: { chart: Chart }) {
  if (chart.kind === 'pie') return <Pie chart={chart} />;
  const W = 480;
  const H = 168;
  const left = 36;
  const bottom = 22;
  const top = 14;
  const all = chart.series.flatMap((s) => s.values);
  const min = Math.min(0, ...all);
  const max = niceMax(Math.max(...all, 0));
  const span = max - min || 1;
  const y = (v: number) => top + (H - top - bottom) * (1 - (v - min) / span);
  const n = chart.labels.length;
  const band = (W - left) / n;
  const x = (i: number) => left + band * i + band / 2;
  const ticks = [min, min + span / 2, max];
  const every = Math.ceil(n / 12);
  const peak = chart.series.length === 1 ? chart.series[0].values.indexOf(Math.max(...chart.series[0].values)) : -1;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={chart.title || `${chart.kind} chart`}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={left} x2={W} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeDasharray={t === 0 ? undefined : '2 3'} />
            <text x={left - 6} y={y(t) + 3} textAnchor="end" fontSize="9.5" fill="rgb(var(--fg-dim-rgb))" fontFamily="'JetBrains Mono', ui-monospace, monospace">{fmt(t)}</text>
          </g>
        ))}
        {chart.kind === 'bar' && chart.series.map((s, si) => {
          const w = Math.min(34, (band * 0.7) / chart.series.length);
          return s.values.map((v, i) => {
            const bx = x(i) - (w * chart.series.length) / 2 + si * w;
            const h = Math.abs(y(v) - y(0));
            const hi = i === peak;
            return (
              <g key={`${si}-${i}`}>
                <rect x={bx} y={Math.min(y(v), y(0))} width={w - 2} height={Math.max(1, h)} rx="2"
                  fill={chart.series.length > 1 ? SERIES[si] : hi ? 'rgb(var(--accent-rgb))' : 'rgb(var(--accent-ink-rgb) / 0.75)'}
                  stroke={hi && chart.series.length === 1 ? 'rgb(var(--accent-ink-rgb))' : 'none'} />
                {n <= 12 && chart.series.length === 1 && (
                  <text x={bx + (w - 2) / 2} y={Math.min(y(v), y(0)) - 4} textAnchor="middle" fontSize="9.5" fill="rgb(var(--fg-dim-rgb))" fontFamily="'JetBrains Mono', ui-monospace, monospace">{fmt(v)}</text>
                )}
              </g>
            );
          });
        })}
        {(chart.kind === 'line' || chart.kind === 'area') && chart.series.map((s, si) => {
          const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
          return (
            <g key={si}>
              {chart.kind === 'area' && <polygon points={`${x(0)},${y(Math.max(min, 0))} ${pts} ${x(n - 1)},${y(Math.max(min, 0))}`} fill={SERIES[si]} opacity="0.14" />}
              <polyline points={pts} fill="none" stroke={SERIES[si]} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
              <circle cx={x(n - 1)} cy={y(s.values[n - 1])} r="3.5" fill={SERIES[si]} />
            </g>
          );
        })}
        {chart.labels.map((label, i) => (i % every === 0 ? (
          <text key={i} x={x(i)} y={H - 6} textAnchor="middle" fontSize="10" fill="rgb(var(--fg-dim-rgb))" fontFamily="'JetBrains Mono', ui-monospace, monospace">{label.length > 8 ? `${label.slice(0, 7)}…` : label}</text>
        ) : null))}
      </svg>
      {chart.series.length > 1 && (
        <div className="mt-1 flex flex-wrap gap-3 text-[11.5px] text-fg-dim">
          {chart.series.map((s, i) => (
            <span key={i} className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: SERIES[i] }} />{s.name || `Series ${i + 1}`}</span>
          ))}
        </div>
      )}
    </div>
  );
}
