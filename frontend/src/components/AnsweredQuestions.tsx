import { tr } from '@/lib/i18n';

interface AnswerEntry {
  question?: string;
  short?: string | null;
  choices?: string[];
  text?: string | null;
  skipped?: boolean;
  recommended?: boolean;
}

export interface AnsweredSummary {
  rows: { label: string; value: string; skipped: boolean; recommended: boolean }[];
  auto: boolean;
  /** The person replied in their own words instead of using the card. */
  words: boolean;
}

const isAskUser = (name: string) => name.toLowerCase().replace(/_/g, ' ').trim() === 'ask user';

const parse = (value?: string): unknown => {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
};

/** The questions an Ask User call put to the person, from its params. */
function askedQuestions(params?: string): { prompt: string; short?: string | null }[] {
  const p = parse(params) as { prompt?: string; questions?: { prompt?: string; question?: string; short?: string }[] } | null;
  if (!p || typeof p !== 'object') return [];
  if (Array.isArray(p.questions) && p.questions.length) {
    return p.questions.map((q) => ({ prompt: String(q?.prompt ?? q?.question ?? ''), short: q?.short }));
  }
  return p.prompt ? [{ prompt: String(p.prompt) }] : [];
}

/** What the person answered on an Ask User card, from the tool call. Null when
 *  the call isn't Ask User or has no answer (cancelled, failed). */
export function answeredSummary(name: string, params?: string, result?: string): AnsweredSummary | null {
  if (!isAskUser(name)) return null;
  const r = parse(result) as { status?: string; answer_type?: string; text?: string; auto_submitted?: boolean; answers?: AnswerEntry[] } | null;
  if (!r || r.status !== 'answered') return null;
  if (Array.isArray(r.answers) && r.answers.length) {
    return {
      auto: !!r.auto_submitted,
      words: false,
      rows: r.answers.map((a) => ({
        label: a.short || a.question || '',
        value: [...(a.choices ?? []), ...(a.text ? [a.text] : [])].join(', '),
        skipped: !!a.skipped,
        recommended: !!a.recommended,
      })),
    };
  }
  // An older answer, or a reply typed instead of using the card.
  const asked = askedQuestions(params);
  const label = asked.length === 1 ? (asked[0].short || asked[0].prompt) : asked.map((q) => q.short || q.prompt).join(' · ');
  return {
    auto: !!r.auto_submitted,
    words: r.answer_type === 'text' && asked.length > 1,
    rows: [{ label, value: String(r.text ?? ''), skipped: false, recommended: false }],
  };
}

/** An answered question card as it stays in the chat: what was asked and what
 *  the person chose. */
export function AnsweredQuestions({ summary }: { summary: AnsweredSummary }) {
  const answered = summary.rows.filter((r) => !r.skipped).length;
  return (
    <section className="w-full max-w-2xl overflow-hidden rounded-xl border border-line" aria-label={tr('ui.answeredQuestions.youAnswered')}>
      <div className="flex items-center gap-2.5 px-3.5 py-2.5">
        <span aria-hidden className="grid h-[18px] w-[18px] flex-none place-items-center rounded-full bg-accent text-accent-foreground">
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 6.3l2.3 2.3 4.7-5" /></svg>
        </span>
        <span className="text-[13.5px] font-semibold text-fg">
          {summary.rows.length > 1 ? tr('ui.answeredQuestions.youAnsweredN', { n: answered }) : tr('ui.answeredQuestions.youAnswered')}
        </span>
        {(summary.auto || summary.words) && (
          <span className="ml-auto font-mono text-[10.5px] text-fg-dim">
            {summary.auto ? tr('ui.answeredQuestions.answeredAutomatically') : tr('ui.answeredQuestions.repliedInWords')}
          </span>
        )}
      </div>
      {summary.rows.map((row, i) => (
        <div key={i} className="flex flex-col gap-0.5 border-t border-line px-3.5 py-2 text-[13.5px] leading-snug sm:flex-row sm:gap-3">
          <span className="text-fg-dim sm:w-48 sm:flex-none">{row.label}</span>
          <span className={row.skipped ? 'italic text-fg-dim' : 'font-medium text-fg'}>
            {row.skipped ? tr('ui.answeredQuestions.skipped') : row.value}
            {row.recommended && !summary.auto && (
              <span className="ml-2 font-mono text-[9.5px] uppercase tracking-[0.1em] text-accent-ink">{tr('ui.answeredQuestions.recommended')}</span>
            )}
          </span>
        </div>
      ))}
    </section>
  );
}
