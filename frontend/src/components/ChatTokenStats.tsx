import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { formatTokens } from '@/components/settings/Usage';
import { tr, trn } from '@/lib/i18n';

export interface ChatTokenTotals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cache_write_tokens: number;
  cost: number;
  unpriced_tokens: number;
}

export interface ChatTokenUsage {
  tracked: boolean;
  prices_available: boolean;
  total: ChatTokenTotals;
  last_turn: ChatTokenTotals | null;
  by_model: (ChatTokenTotals & { provider: string; model: string })[];
  context: { tokens: number; window: number; provider?: string; model?: string } | null;
}

/** Small amounts keep two significant digits: a chat often costs a fraction of a cent. */
export function chatCost(n: number): string {
  if (n <= 0) return '$0.00';
  if (n < 0.01) return `$${Number(n.toPrecision(2))}`;
  return `$${n.toFixed(2)}`;
}

const tokensIn = (t: ChatTokenTotals) => t.prompt_tokens;
const tokensOf = (t: ChatTokenTotals) => t.prompt_tokens + t.completion_tokens;

/** Fetches this chat's token counts, again each time a reply finishes. */
export function useChatTokenUsage(sessionId: string | null | undefined, busy: boolean) {
  const [usage, setUsage] = useState<ChatTokenUsage | null>(null);
  const current = useRef(sessionId);
  current.current = sessionId;

  useEffect(() => {
    setUsage(null);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || busy) return;
    let cancelled = false;
    api.get<ChatTokenUsage>(`/sessions/${sessionId}/usage`)
      .then(({ data }) => { if (!cancelled && current.current === sessionId && data) setUsage(data); })
      .catch(() => { /* No counts this time; the section shows its empty text. */ });
    return () => { cancelled = true; };
  }, [sessionId, busy]);

  return usage;
}

/** The header's short figure: tokens so far in this chat. */
export function tokenSummary(usage: ChatTokenUsage | null): string {
  if (!usage?.tracked) return '';
  return formatTokens(tokensOf(usage.total));
}

function cost(t: ChatTokenTotals): string {
  if (t.unpriced_tokens > 0 && t.unpriced_tokens >= tokensOf(t)) return '—';
  return chatCost(t.cost);
}

export function ChatTokenStats({ usage }: { usage: ChatTokenUsage | null }) {
  const term = 'font-mono text-[10.5px] text-fg-dim';
  const context = usage?.context && usage.context.tokens > 0 ? usage.context : null;
  const fraction = context && context.window > 0 ? Math.min(1, context.tokens / context.window) : 0;
  const percent = Math.round(fraction * 100);

  const meter = context && (
    <div className="mb-3">
      <div className="mb-1.5 flex items-baseline justify-between gap-2 text-[12.5px]">
        <span className={term}>{tr('ui.chatTokens.context')}</span>
        <span className="font-mono text-[11.5px]">
          {context.window > 0
            ? tr('ui.chatTokens.tokensOfWindow', { tokens: formatTokens(context.tokens), window: formatTokens(context.window), percent })
            : formatTokens(context.tokens)}
        </span>
      </div>
      {context.window > 0 && (
        <div
          className="h-1.5 overflow-hidden rounded-full bg-panel-2"
          role="meter"
          aria-label={tr('ui.chatTokens.contextUsed')}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div
            className={cn('h-full rounded-full', fraction >= 0.9 ? 'bg-danger' : fraction >= 0.7 ? 'bg-warn' : 'bg-accent')}
            style={{ width: `${Math.max(percent, 2)}%` }}
          />
        </div>
      )}
      {fraction >= 0.7 && (
        <p className="mt-1.5 text-[11.5px] leading-snug text-fg-dim">{tr('ui.chatTokens.nearlyFull')}</p>
      )}
    </div>
  );

  if (!usage?.tracked) {
    return (
      <div>
        {meter}
        <p className="text-[12.5px] leading-relaxed text-fg-dim">{tr('ui.chatTokens.countsStartWithTheNext')}</p>
      </div>
    );
  }

  const { total, last_turn: last } = usage;
  const rows: { label: string; chat: string; last: string; hint?: string }[] = [
    { label: tr('ui.chatTokens.input'), chat: formatTokens(tokensIn(total)), last: last ? formatTokens(tokensIn(last)) : '—' },
    { label: tr('ui.chatTokens.output'), chat: formatTokens(total.completion_tokens), last: last ? formatTokens(last.completion_tokens) : '—' },
  ];
  if (total.cached_tokens > 0 || total.cache_write_tokens > 0) {
    rows.push({
      label: tr('ui.chatTokens.cached'),
      chat: formatTokens(total.cached_tokens),
      last: last ? formatTokens(last.cached_tokens) : '—',
      hint: tr('ui.chatTokens.cachedHint'),
    });
  }
  rows.push({
    label: tr('ui.chatTokens.cost'),
    chat: cost(total),
    last: last ? cost(last) : '—',
    hint: tr('ui.chatTokens.costHint'),
  });

  return (
    <div>
      {meter}
      <table className="w-full text-[12.5px]">
        <thead>
          <tr>
            <th scope="col" className="sr-only">{tr('ui.chatTokens.kind')}</th>
            <th scope="col" className={cn(term, 'pb-1 text-right font-normal')}>{tr('ui.chatTokens.thisChat')}</th>
            <th scope="col" className={cn(term, 'pb-1 pl-3 text-right font-normal')}>{tr('ui.chatTokens.lastReply')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} title={row.hint}>
              <th scope="row" className={cn(term, 'py-0.5 text-left font-normal')}>{row.label}</th>
              <td className="py-0.5 text-right font-mono tabular-nums">{row.chat}</td>
              <td className="py-0.5 pl-3 text-right font-mono tabular-nums text-fg-dim">{row.last}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[11.5px] leading-snug text-fg-dim">
        {trn(total.calls, 'ui.chatTokens.modelCalls_one', 'ui.chatTokens.modelCalls_other')}
        {usage.by_model.length > 1 && ` · ${trn(usage.by_model.length, 'ui.chatTokens.models_one', 'ui.chatTokens.models_other')}`}
        {total.unpriced_tokens > 0 && total.unpriced_tokens < tokensOf(total) && ` · ${tr('ui.chatTokens.someUnpriced')}`}
        {total.unpriced_tokens > 0 && total.unpriced_tokens >= tokensOf(total) && ` · ${tr('ui.chatTokens.noPrice')}`}
      </p>
      {usage.by_model.length > 1 && (
        <ul className="mt-1.5 flex flex-col gap-0.5 text-[11.5px] text-fg-dim">
          {usage.by_model.map((m) => (
            <li key={`${m.provider}|${m.model}`} className="flex gap-2">
              <span className="min-w-0 flex-1 truncate" title={`${m.provider} · ${m.model}`}>{m.model || m.provider}</span>
              <span className="font-mono tabular-nums">{formatTokens(tokensOf(m))}</span>
              <span className="w-16 text-right font-mono tabular-nums">{cost(m)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
