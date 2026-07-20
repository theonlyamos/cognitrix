import { useCallback, useEffect, useId, useRef, useState } from 'react';

export interface QuestionOption {
  id: string;
  label: string;
  description?: string | null;
}

export interface QuestionRequest {
  request_id: string;
  session_id?: string;
  prompt: string;
  details?: string | null;
  options: QuestionOption[];
  allow_free_text: boolean;
  recommended_option_id?: string | null;
  auto_submit_seconds?: number | null;
  auto_submit_at?: string | null;
}

type QuestionAnswer = { option_id: string } | { text: string };

interface QuestionCardProps {
  question: QuestionRequest;
  onAnswer: (answer: QuestionAnswer) => Promise<void> | void;
  onCancel: () => Promise<void> | void;
  onStopTimer: () => Promise<void> | void;
}

export function QuestionCard({
  question,
  onAnswer,
  onCancel,
  onStopTimer,
}: QuestionCardProps) {
  const headingId = useId();
  const [freeText, setFreeText] = useState('');
  const [remaining, setRemaining] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const automaticSubmissionStarted = useRef(false);
  const answerRef = useRef(onAnswer);
  answerRef.current = onAnswer;

  const submit = useCallback(async (answer: QuestionAnswer) => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onAnswer(answer);
    } catch {
      setSubmitting(false);
      setError('Could not send your answer. Try again.');
    }
  }, [onAnswer, submitting]);

  useEffect(() => {
    automaticSubmissionStarted.current = false;
    const deadline = question.auto_submit_at
      ? Date.parse(question.auto_submit_at)
      : Number.NaN;
    if (!Number.isFinite(deadline) || !question.recommended_option_id) {
      setRemaining(null);
      return;
    }

    const tick = () => {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setRemaining(seconds);
      if (seconds === 0 && !automaticSubmissionStarted.current) {
        automaticSubmissionStarted.current = true;
        setSubmitting(true);
        void Promise.resolve(answerRef.current({
          option_id: question.recommended_option_id!,
        })).catch(() => {
          automaticSubmissionStarted.current = false;
          setSubmitting(false);
          setError('The automatic answer could not be sent. Choose an option to continue.');
        });
      }
    };
    tick();
    const interval = window.setInterval(tick, 250);
    return () => window.clearInterval(interval);
  }, [question.auto_submit_at, question.recommended_option_id, question.request_id]);

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
      setError('Could not stop the countdown. Try again.');
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
      setError('Could not cancel the question. Try again.');
    }
  };

  return (
    <section
      role="group"
      aria-labelledby={headingId}
      className="animate-rise mx-4 my-4 border-l-2 border-accent bg-accent/5 px-3 py-3 font-mono text-[12px] sm:mx-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <span className="text-accent-ink">? input needed</span>
        {remaining !== null && (
          <div className="flex items-center gap-2 text-[10px] tracking-wide text-fg-dim">
            <span role="timer" aria-live="off">Using recommendation in {remaining}s</span>
            <button
              type="button"
              onClick={() => void stopTimer()}
              disabled={submitting}
              className="min-h-11 rounded border border-line px-2 py-1 text-fg-dim transition-colors hover:border-fg-dim hover:text-fg disabled:opacity-50 md:min-h-0"
            >
              Stop countdown
            </button>
          </div>
        )}
      </div>

      <h2 id={headingId} className="mt-2 text-sm font-semibold text-fg">
        {question.prompt}
      </h2>
      {question.details && (
        <p className="mt-1 max-w-2xl font-sans text-[13px] leading-relaxed text-fg-dim">
          {question.details}
        </p>
      )}

      {question.options.length > 0 && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {question.options.map((option) => {
            const recommended = option.id === question.recommended_option_id;
            return (
              <button
                key={option.id}
                type="button"
                disabled={submitting}
                onClick={() => void submit({ option_id: option.id })}
                className="group min-h-11 rounded border border-line bg-panel/70 px-3 py-2 text-left transition-colors hover:border-accent hover:bg-panel disabled:cursor-wait disabled:opacity-50 md:min-h-0"
              >
                <span className="flex items-center justify-between gap-3 text-fg">
                  <span>{option.label}</span>
                  {recommended && (
                    <span className="rounded border border-accent/50 px-1.5 py-0.5 text-[9px] uppercase tracking-[0.14em] text-accent-ink">
                      recommended
                    </span>
                  )}
                </span>
                {option.description && (
                  <span className="mt-1 block font-sans text-[12px] leading-snug text-fg-dim">
                    {option.description}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {question.allow_free_text && (
        <form
          className="mt-3 flex flex-col gap-2 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault();
            const text = freeText.trim();
            if (text) void submit({ text });
          }}
        >
          <label className="min-w-0 flex-1">
            <span className="sr-only">Your answer</span>
            <input
              aria-label="Your answer"
              value={freeText}
              maxLength={4000}
              disabled={submitting}
              onChange={(event) => setFreeText(event.target.value)}
              placeholder="Type a different answer…"
              className="h-11 w-full rounded border border-line bg-panel px-3 font-sans text-[13px] text-fg outline-none transition-colors placeholder:text-fg-dim focus:border-accent md:h-9"
            />
          </label>
          <button
            type="submit"
            disabled={submitting || !freeText.trim()}
            className="min-h-11 rounded border border-accent bg-accent px-3 py-1 text-[11px] text-accent-foreground transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50 md:min-h-0"
          >
            Submit answer
          </button>
        </form>
      )}

      <div className="mt-3 flex min-h-5 items-center justify-between gap-3">
        <div aria-live="polite" className="font-sans text-[12px] text-danger-ink">
          {error}
        </div>
        <button
          type="button"
          disabled={submitting}
          onClick={() => void cancel()}
          className="min-h-11 flex-none rounded px-2 py-1 text-[11px] text-fg-dim transition-colors hover:text-danger-ink disabled:opacity-50 md:min-h-0"
        >
          Cancel question
        </button>
      </div>
    </section>
  );
}
