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
        label: a.question || a.short || '',
        value: [...(a.choices ?? []), ...(a.text ? [a.text] : [])].join(', '),
        skipped: !!a.skipped,
        recommended: !!a.recommended,
      })),
    };
  }
  // An older answer, or a reply typed instead of using the card.
  const asked = askedQuestions(params);
  const label = asked.length === 1 ? (asked[0].prompt || asked[0].short || '') : asked.map((q) => q.short || q.prompt).join(' · ');
  return {
    auto: !!r.auto_submitted,
    words: r.answer_type === 'text' && asked.length > 1,
    rows: [{ label, value: String(r.text ?? ''), skipped: false, recommended: false }],
  };
}

/** An answered question card as it stays in the chat: each question, with
 *  what the person chose right under it. */
export function AnsweredQuestions({ summary }: { summary: AnsweredSummary }) {
  const many = summary.rows.length > 1;
  const answered = summary.rows.filter((r) => !r.skipped).length;
  return (
    <section
      className="w-full max-w-xl rounded-xl border border-line bg-panel/60 px-3.5 py-2.5"
      aria-label={tr('ui.answeredQuestions.youAnswered')}
    >
      <div className="mb-1.5 flex items-center gap-2 font-mono text-[10.5px] uppercase tracking-[0.1em] text-fg-dim">
        <span>{many ? tr('ui.answeredQuestions.youAnsweredN', { n: answered }) : tr('ui.answeredQuestions.youAnswered')}</span>
        {(summary.auto || summary.words) && (
          <span className="ml-auto normal-case tracking-normal">
            {summary.auto ? tr('ui.answeredQuestions.answeredAutomatically') : tr('ui.answeredQuestions.repliedInWords')}
          </span>
        )}
      </div>
      <dl className="divide-y divide-line">
        {summary.rows.map((row, i) => (
          <div key={i} className="py-1.5 first:pt-0 last:pb-0">
            <dt className="text-[13px] leading-snug text-fg-dim">{row.label}</dt>
            <dd className="mt-1 flex items-start gap-2 text-[14px] leading-snug">
              {row.skipped ? (
                <span className="italic text-fg-dim">{tr('ui.answeredQuestions.skipped')}</span>
              ) : (
                <>
                  <span aria-hidden className="mt-[3px] grid h-3.5 w-3.5 flex-none place-items-center rounded-full bg-accent text-accent-foreground">
                    <svg width="8" height="8" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 6.3l2.3 2.3 4.7-5" /></svg>
                  </span>
                  <span className="min-w-0 whitespace-pre-wrap break-words font-medium text-fg">
                    {row.value}
                    {row.recommended && !summary.auto && (
                      <span className="ml-2 align-[1px] font-mono text-[9.5px] font-normal uppercase tracking-[0.1em] text-accent-ink">{tr('ui.answeredQuestions.recommended')}</span>
                    )}
                  </span>
                </>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
