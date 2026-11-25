import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { PanelButton, PanelCard } from '@/components/cards/PanelCard';
import { cn } from '@/lib/utils';
import { errorMessage } from '@/lib/api';
import { answerSignInRequest } from '@/lib/sandbox';
import { tr } from '@/lib/i18n';

export interface QuestionOption {
  id: string;
  label: string;
  description?: string | null;
}

/** One question on the card. A card holds one to four, shown one at a time. */
export interface QuestionItem {
  id: string;
  prompt: string;
  details?: string | null;
  /** A one or two word label for the answered summary ("Database"). */
  short?: string | null;
  options: QuestionOption[];
  multiple?: boolean;
  allow_free_text?: boolean;
  recommended_option_id?: string | null;
  recommended_option_ids?: string[];
}

export interface QuestionRequest {
  request_id: string;
  session_id?: string;
  prompt: string;
  details?: string | null;
  options: QuestionOption[];
  allow_free_text: boolean;
  recommended_option_id?: string | null;
  multiple?: boolean;
  /** Every question on the card; older servers send only the fields above. */
  questions?: QuestionItem[];
  auto_submit_seconds?: number | null;
  auto_submit_at?: string | null;
  /** Ask For Sign-in: a masked field whose value goes straight to Saved sign-ins, never to the agent. */
  secret?: { name: string; kind: 'token' | 'basic'; hosts?: string[]; description?: string; replaces?: boolean } | null;
}

export interface CardAnswer { question_id: string; option_ids?: string[]; text?: string; skipped?: boolean }
export type QuestionAnswer = { option_id: string } | { text: string } | { answers: CardAnswer[] };

interface QuestionCardProps {
  question: QuestionRequest;
  onAnswer: (answer: QuestionAnswer) => Promise<void> | void;
  onCancel: () => Promise<void> | void;
  onStopTimer: () => Promise<void> | void;
}

/** The card's questions, also for a request from an older server. */
export function questionItems(question: QuestionRequest): QuestionItem[] {
  if (question.questions?.length) return question.questions;
  return [{
    id: 'q1',
    prompt: question.prompt,
    details: question.details,
    options: question.options,
    multiple: !!question.multiple,
    allow_free_text: question.allow_free_text,
    recommended_option_id: question.recommended_option_id,
  }];
}

const recommendedOf = (item: QuestionItem) => item.recommended_option_ids?.length
  ? item.recommended_option_ids
  : item.recommended_option_id ? [item.recommended_option_id] : [];

/** Every question answered with the agent's recommendation. */
const recommendedAnswers = (items: QuestionItem[]): CardAnswer[] =>
  items.map((item) => ({ question_id: item.id, option_ids: recommendedOf(item) }));

export function QuestionCard(props: QuestionCardProps) {
  if (props.question.secret) return <SignInCard {...props} />;
  return <AskCard key={props.question.request_id} {...props} />;
}

type Draft = { picks: string[]; text: string; other: boolean; skipped: boolean };

function AskCard({ question, onAnswer, onCancel, onStopTimer }: QuestionCardProps) {
  const headingId = useId();
  const items = questionItems(question);
  const total = items.length;
  const [step, setStep] = useState(0);
  // The recommendation starts picked, so accepting it is one press.
  const [drafts, setDrafts] = useState<Draft[]>(() => items.map((item) => ({
    picks: recommendedOf(item).slice(0, item.multiple ? undefined : 1),
    text: '',
    other: !item.options.length,
    skipped: false,
  })));
  const [remaining, setRemaining] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const automaticSubmissionStarted = useRef(false);
  const answerRef = useRef(onAnswer);
  answerRef.current = onAnswer;
  const otherRef = useRef<HTMLTextAreaElement>(null);

  const item = items[Math.min(step, total - 1)];
  const draft = drafts[Math.min(step, total - 1)];
  const hasRecommendation = items.every((i) => recommendedOf(i).length > 0);

  useEffect(() => {
    automaticSubmissionStarted.current = false;
    const deadline = question.auto_submit_at ? Date.parse(question.auto_submit_at) : Number.NaN;
    if (!Number.isFinite(deadline) || !hasRecommendation) {
      setRemaining(null);
      return;
    }
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setRemaining(seconds);
      if (seconds === 0 && !automaticSubmissionStarted.current) {
        automaticSubmissionStarted.current = true;
        setSubmitting(true);
        void Promise.resolve(answerRef.current({ answers: recommendedAnswers(items) })).catch(() => {
          automaticSubmissionStarted.current = false;
          setSubmitting(false);
          setError(tr('ui.questionCard.theAutomaticAnswerCouldNot'));
        });
      }
    };
    tick();
    const interval = window.setInterval(tick, 250);
    return () => window.clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question.auto_submit_at, question.request_id, hasRecommendation]);

  const update = (change: Partial<Draft>) => setDrafts((all) => all.map((d, i) => (i === step ? { ...d, ...change } : d)));

  const pick = (optionId: string) => {
    if (submitting) return;
    if (item.multiple) {
      update({ skipped: false, picks: draft.picks.includes(optionId) ? draft.picks.filter((p) => p !== optionId) : [...draft.picks, optionId] });
    } else {
      update({ skipped: false, picks: [optionId], other: false, text: '' });
    }
  };

  const openOther = () => {
    if (submitting) return;
    update(item.multiple ? { other: true, skipped: false } : { other: true, picks: [], skipped: false });
    window.setTimeout(() => otherRef.current?.focus(), 0);
  };

  const answered = (d: Draft) => d.picks.length > 0 || (d.other && !!d.text.trim());

  const submit = useCallback(async (answer: QuestionAnswer) => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onAnswer(answer);
    } catch {
      setSubmitting(false);
      setError(tr('ui.questionCard.couldNotSendYourAnswer'));
    }
  }, [onAnswer, submitting]);

  const finish = (all: Draft[]) => void submit({
    answers: items.map((it, i) => {
      const d = all[i];
      if (d.skipped || !answered(d)) return { question_id: it.id, skipped: true };
      const text = d.other ? d.text.trim() : '';
      return { question_id: it.id, ...(d.picks.length ? { option_ids: d.picks } : {}), ...(text ? { text } : {}) };
    }),
  });

  const next = (skip = false) => {
    if (submitting) return;
    const all = drafts.map((d, i) => (i === step ? { ...d, skipped: skip } : d));
    setDrafts(all);
    if (step < total - 1) setStep(step + 1);
    else finish(all);
  };

  const stopTimer = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onStopTimer();
      setRemaining(null);
      setSubmitting(false);
    } catch {
      setSubmitting(false);
      setError(tr('ui.questionCard.couldNotStopTheCountdown'));
    }
  };

  const cancel = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onCancel();
    } catch {
      setSubmitting(false);
      setError(tr('ui.questionCard.couldNotCancelTheQuestion'));
    }
  };

  // Number keys pick an option while the card (not a text box) has focus.
  const onKeyDown = (event: React.KeyboardEvent) => {
    const target = event.target as HTMLElement;
    if (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT' || event.metaKey || event.ctrlKey || event.altKey) return;
    const n = Number(event.key);
    if (Number.isInteger(n) && n >= 1 && n <= item.options.length) {
      event.preventDefault();
      pick(item.options[n - 1].id);
    } else if (event.key === 'Enter' && answered(draft)) {
      event.preventDefault();
      next();
    }
  };

  const last = step === total - 1;
  const ready = answered(draft);
  const Mark = item.multiple ? CheckMark : RadioMark;

  return (
    <div className="mx-auto my-4 w-[calc(100%-3rem)] max-w-[720px]">
      <section
        role="group"
        aria-labelledby={headingId}
        onKeyDown={onKeyDown}
        className="animate-rise overflow-hidden rounded-xl border border-line bg-panel"
      >
        <div className="flex items-center gap-3 px-4 pt-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-fg-dim">
            {total > 1 ? tr('ui.questionCard.questionNOfTotal', { n: step + 1, total }) : tr('ui.questionCard.question')}
          </span>
          {total > 1 && (
            <span aria-hidden className="flex gap-1">
              {items.map((it, i) => (
                <i key={it.id} className={cn('block h-1 w-[18px] rounded-sm', i < step ? 'bg-accent-ink' : i === step ? 'bg-fg' : 'bg-panel-2')} />
              ))}
            </span>
          )}
          <span className="grow" />
          <button
            type="button"
            onClick={() => void cancel()}
            disabled={submitting}
            className="min-h-11 px-1 font-mono text-[10.5px] text-fg-dim transition-colors hover:text-danger-ink disabled:opacity-50 md:min-h-0"
          >
            {tr('ui.questionCard.cancelQuestion')}
          </button>
        </div>
        <h2 id={headingId} className="px-4 pt-1.5 text-[16px] font-semibold leading-snug text-fg">{item.prompt}</h2>
        {item.details && <p className="px-4 pt-1 text-[13px] leading-relaxed text-fg-dim">{item.details}</p>}
        {item.multiple && (
          <p className="px-4 pt-1.5 font-mono text-[11px] text-fg-dim">
            {tr('ui.questionCard.pickAnyNSelected', { n: draft.picks.length })}
          </p>
        )}

        <div className="flex flex-col gap-1.5 px-3 pb-1 pt-3" role={item.multiple ? 'group' : 'radiogroup'} aria-labelledby={headingId}>
          {item.options.map((option, i) => {
            const on = draft.picks.includes(option.id);
            const recommended = recommendedOf(item).includes(option.id);
            return (
              <button
                key={option.id}
                type="button"
                role={item.multiple ? 'checkbox' : 'radio'}
                aria-checked={on}
                disabled={submitting}
                onClick={() => pick(option.id)}
                className={cn(
                  'flex min-h-11 w-full items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors disabled:cursor-wait disabled:opacity-60',
                  on ? 'border-accent-ink bg-accent-ink/10' : 'border-line hover:border-fg-dim',
                )}
              >
                <Mark on={on} />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2 text-[14px] font-medium text-fg">
                    {option.label}
                    {recommended && (
                      <span className="rounded border border-accent-ink/50 px-1.5 py-px font-mono text-[9px] uppercase tracking-[0.12em] text-accent-ink">
                        {tr('ui.questionCard.recommended')}
                      </span>
                    )}
                  </span>
                  {option.description && <span className="mt-0.5 block text-[12.5px] leading-snug text-fg-dim">{option.description}</span>}
                </span>
                {i < 9 && <span aria-hidden className="mt-0.5 hidden font-mono text-[11px] text-fg-dim md:inline">{i + 1}</span>}
              </button>
            );
          })}

          {item.options.length > 0 && !draft.other && (
            <button
              type="button"
              disabled={submitting}
              onClick={openOther}
              className="flex min-h-11 w-full items-center gap-3 rounded-lg border border-line px-3 py-2.5 text-left text-[14px] text-fg-dim transition-colors hover:border-fg-dim disabled:opacity-60"
            >
              <Mark on={false} />
              {tr('ui.questionCard.somethingElse')}
            </button>
          )}
          {draft.other && (
            <label className={cn('block', item.options.length > 0 && 'rounded-lg border border-accent-ink p-2')}>
              <span className="sr-only">{tr('ui.questionCard.yourAnswer')}</span>
              <textarea
                ref={otherRef}
                aria-label={tr('ui.questionCard.yourAnswer')}
                value={draft.text}
                maxLength={4000}
                rows={item.options.length ? 2 : 3}
                disabled={submitting}
                autoFocus={!item.options.length && step > 0}
                onChange={(event) => update({ text: event.target.value, skipped: false })}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey && draft.text.trim()) {
                    event.preventDefault();
                    next();
                  }
                }}
                placeholder={item.options.length ? tr('ui.questionCard.typeADifferentAnswer') : tr('ui.questionCard.typeYourAnswer')}
                className={cn(
                  'block w-full resize-none bg-bg px-3 py-2 text-[14px] leading-relaxed text-fg outline-none placeholder:text-fg-dim',
                  item.options.length ? 'rounded-md' : 'rounded-lg border border-line focus:border-accent-ink',
                )}
              />
            </label>
          )}
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-line px-3 py-2.5 sm:pl-4">
          {remaining !== null ? (
            <span className="flex items-center gap-2 font-mono text-[10.5px] text-fg-dim">
              <span role="timer" aria-live="off">{tr('ui.questionCard.usingRecommendationIn')}{' '}{remaining}s</span>
              <PanelButton kind="quiet" onClick={() => void stopTimer()} disabled={submitting}>{tr('ui.questionCard.stopCountdown')}</PanelButton>
            </span>
          ) : (
            <PanelButton kind="quiet" disabled={submitting} onClick={() => next(true)}>{tr('ui.questionCard.skip')}</PanelButton>
          )}
          <div aria-live="polite" className="text-[12px] text-danger-ink">{error}</div>
          <span className="grow" />
          {step > 0 && (
            <PanelButton disabled={submitting} onClick={() => setStep(step - 1)}>{tr('ui.questionCard.back')}</PanelButton>
          )}
          <PanelButton kind="primary" disabled={submitting || !ready} onClick={() => next()}>
            {last ? (total > 1 ? tr('ui.questionCard.sendAnswers') : tr('ui.questionCard.submitAnswer')) : tr('ui.questionCard.next')}
          </PanelButton>
        </div>
      </section>
    </div>
  );
}

function RadioMark({ on }: { on: boolean }) {
  return (
    <span aria-hidden className={cn('mt-0.5 grid h-4 w-4 flex-none place-items-center rounded-full border-[1.5px]', on ? 'border-accent-ink' : 'border-fg-dim/50')}>
      {on && <span className="h-2 w-2 rounded-full bg-accent-ink" />}
    </span>
  );
}

function CheckMark({ on }: { on: boolean }) {
  return (
    <span aria-hidden className={cn('mt-0.5 grid h-4 w-4 flex-none place-items-center rounded border-[1.5px]', on ? 'border-accent bg-accent text-accent-foreground' : 'border-fg-dim/50')}>
      {on && (
        <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 6.3l2.3 2.3 4.7-5" /></svg>
      )}
    </span>
  );
}

/** Ask For Sign-in: a masked field, saved straight to Saved sign-ins. */
function SignInCard({ question, onAnswer, onCancel }: QuestionCardProps) {
  const headingId = useId();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<void> | void, failure: string) => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await action();
    } catch {
      setSubmitting(false);
      setError(failure);
    }
  };

  return (
    <div className="mx-auto my-4 w-[calc(100%-3rem)] max-w-[720px]">
      <PanelCard
        role="group"
        labelledBy={headingId}
        titleId={headingId}
        tag="sign-in"
        title={question.prompt}
        subtitle={question.details || undefined}
        footer={(
          <>
            <div aria-live="polite" className="text-[12px] text-danger-ink">{error}</div>
            <span className="grow" />
            <PanelButton kind="quiet" disabled={submitting} onClick={() => void run(onCancel, tr('ui.questionCard.couldNotCancelTheQuestion'))}>{tr('ui.questionCard.donTShare')}</PanelButton>
          </>
        )}
      >
        <SecretForm
          question={question}
          disabled={submitting}
          onSaved={() => void run(() => onAnswer({ option_id: 'saved' }), tr('ui.questionCard.couldNotSendYourAnswer'))}
        />
      </PanelCard>
    </div>
  );
}

const fieldCls = 'h-11 w-full rounded-md border border-line bg-panel px-3 font-mono text-[13px] text-fg outline-none transition-colors placeholder:text-fg-dim focus:border-accent md:h-9';

function SecretForm({ question, disabled, onSaved }: { question: QuestionRequest; disabled: boolean; onSaved: () => void }) {
  const secret = question.secret!;
  const [value, setValue] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const basic = secret.kind === 'basic';
  const ready = basic ? !!username.trim() && !!password : !!value.trim();

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || saving) return;
    setSaving(true);
    setError('');
    try {
      await answerSignInRequest(question.request_id, basic ? { username, password } : { value });
      setValue('');
      setPassword('');
      onSaved();
    } catch (err) {
      setSaving(false);
      setError(errorMessage(err, tr('ui.questionCard.couldnTSaveItTry')));
    }
  }

  return (
    <form onSubmit={(e) => void save(e)} className="flex flex-col gap-2" autoComplete="off">
      {basic ? (
        <div className="flex flex-col gap-2 sm:flex-row">
          <input aria-label={tr('ui.questionCard.username')} value={username} onChange={(e) => setUsername(e.target.value)} placeholder={tr('ui.questionCard.username')}
            disabled={disabled || saving} className={fieldCls} autoComplete="off" />
          <input aria-label={tr('ui.questionCard.password')} type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            placeholder={tr('ui.questionCard.password')} disabled={disabled || saving} className={fieldCls} autoComplete="new-password" />
        </div>
      ) : (
        <input aria-label={secret.name} type="password" value={value} onChange={(e) => setValue(e.target.value)}
          placeholder={tr('ui.questionCard.nameValue', { name: secret.name })} disabled={disabled || saving} className={fieldCls} autoComplete="new-password" />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px] text-fg-dim">
          {tr('ui.questionCard.savedAs')}{' '}<span className="font-mono">{secret.name}</span>{' '}{tr('ui.questionCard.inSettingsSavedSignIns')}
        </span>
        <span className="grow" />
        <PanelButton type="submit" kind="primary" disabled={disabled || saving || !ready}>{saving ? tr('ui.questionCard.saving') : tr('ui.questionCard.save')}</PanelButton>
      </div>
      {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
    </form>
  );
}
