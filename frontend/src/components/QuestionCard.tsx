import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { PanelButton, PanelCard } from '@/components/cards/PanelCard';
import { cn } from '@/lib/utils';
import { errorMessage } from '@/lib/api';
import { answerSignInRequest } from '@/lib/sandbox';

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
  /** Ask For Sign-in: a masked field whose value goes straight to Saved sign-ins, never to the agent. */
  secret?: { name: string; kind: 'token' | 'basic'; hosts?: string[]; description?: string; replaces?: boolean } | null;
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
    <div className="mx-auto my-4 w-[calc(100%-3rem)] max-w-[720px]">
      <PanelCard
        role="group"
        labelledBy={headingId}
        titleId={headingId}
        tag={question.secret ? 'sign-in' : '? question'}
        title={question.prompt}
        subtitle={question.details || undefined}
        footer={(
          <>
            {remaining !== null && (
              <div className="flex items-center gap-2 font-mono text-[10.5px] tracking-wide text-fg-dim">
                <span role="timer" aria-live="off">Using recommendation in {remaining}s</span>
                <PanelButton onClick={() => void stopTimer()} disabled={submitting}>Stop countdown</PanelButton>
              </div>
            )}
            <div aria-live="polite" className="text-[12px] text-danger-ink">{error}</div>
            <span className="grow" />
            <PanelButton kind="quiet" disabled={submitting} onClick={() => void cancel()}>{question.secret ? "Don't share" : 'Cancel question'}</PanelButton>
          </>
        )}
      >
        {question.secret && (
          <SecretForm
            question={question}
            disabled={submitting}
            onSaved={() => void submit({ option_id: 'saved' })}
          />
        )}

        {!question.secret && question.options.length > 0 && (
          <div className="grid gap-2 sm:grid-cols-2">
            {question.options.map((option) => {
              const recommended = option.id === question.recommended_option_id;
              return (
                <button
                  key={option.id}
                  type="button"
                  disabled={submitting}
                  onClick={() => void submit({ option_id: option.id })}
                  className={cn(
                    'group min-h-11 rounded-md border bg-panel px-3 py-2 text-left transition-colors hover:border-accent disabled:cursor-wait disabled:opacity-50 md:min-h-0',
                    recommended ? 'border-accent-ink/60' : 'border-line',
                  )}
                >
                  <span className="flex items-center justify-between gap-3 text-[13.5px] font-medium text-fg">
                    <span>{option.label}</span>
                    {recommended && (
                      <span className="rounded border border-accent/50 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.14em] text-accent-ink">
                        recommended
                      </span>
                    )}
                  </span>
                  {option.description && (
                    <span className="mt-1 block text-[12px] leading-snug text-fg-dim">
                      {option.description}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}

        {!question.secret && question.allow_free_text && (
          <form
            className={cn('flex flex-col gap-2 sm:flex-row', question.options.length > 0 && 'mt-3')}
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
                className="h-11 w-full rounded-md border border-line bg-panel px-3 text-[13px] text-fg outline-none transition-colors placeholder:text-fg-dim focus:border-accent md:h-9"
              />
            </label>
            <PanelButton type="submit" kind="primary" disabled={submitting || !freeText.trim()}>Submit answer</PanelButton>
          </form>
        )}
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
      setError(errorMessage(err, "Couldn't save it. Try again."));
    }
  }

  return (
    <form onSubmit={(e) => void save(e)} className="flex flex-col gap-2" autoComplete="off">
      {basic ? (
        <div className="flex flex-col gap-2 sm:flex-row">
          <input aria-label="Username" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Username"
            disabled={disabled || saving} className={fieldCls} autoComplete="off" />
          <input aria-label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            placeholder="Password" disabled={disabled || saving} className={fieldCls} autoComplete="new-password" />
        </div>
      ) : (
        <input aria-label={secret.name} type="password" value={value} onChange={(e) => setValue(e.target.value)}
          placeholder={`${secret.name} value`} disabled={disabled || saving} className={fieldCls} autoComplete="new-password" />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px] text-fg-dim">
          Saved as <span className="font-mono">{secret.name}</span> in Settings, Saved sign-ins. The agent only gets the name.
        </span>
        <span className="grow" />
        <PanelButton type="submit" kind="primary" disabled={disabled || saving || !ready}>{saving ? 'Saving…' : 'Save'}</PanelButton>
      </div>
      {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
    </form>
  );
}
