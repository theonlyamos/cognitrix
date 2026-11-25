import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { QuestionCard, type QuestionRequest } from './QuestionCard';


const question = (overrides: Partial<QuestionRequest> = {}): QuestionRequest => ({
  request_id: 'question-1',
  session_id: 'session-1',
  prompt: 'How should I continue?',
  details: 'Choose the approach that best fits this run.',
  options: [
    { id: 'quick', label: 'Quick pass', description: 'Use the current context.' },
    { id: 'deep', label: 'Deep research', description: 'Gather additional sources.' },
  ],
  allow_free_text: false,
  recommended_option_id: 'deep',
  auto_submit_seconds: null,
  auto_submit_at: null,
  ...overrides,
});


describe('QuestionCard', () => {
  afterEach(() => vi.useRealTimers());

  it('starts on the recommendation and sends the picked option', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    render(
      <QuestionCard
        question={question()}
        onAnswer={onAnswer}
        onCancel={vi.fn()}
        onStopTimer={vi.fn()}
      />,
    );

    expect(screen.getByRole('group', { name: 'How should I continue?' })).toHaveClass('rounded-xl', 'border-line');
    expect(screen.getByText('recommended')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Deep research/i })).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(screen.getByRole('radio', { name: /Quick pass/i }));
    expect(screen.getByRole('radio', { name: /Deep research/i })).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(screen.getByRole('button', { name: 'Submit answer' }));
    expect(onAnswer).toHaveBeenCalledWith({ answers: [{ question_id: 'q1', option_ids: ['quick'] }] });
  });

  it('accepts free text and keeps mobile actions touch-sized', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    render(
      <QuestionCard
        question={question({ options: [], allow_free_text: true, recommended_option_id: null })}
        onAnswer={onAnswer}
        onCancel={vi.fn()}
        onStopTimer={vi.fn()}
      />,
    );

    await userEvent.type(screen.getByRole('textbox', { name: 'Your answer' }), 'Use a local model');
    const submit = screen.getByRole('button', { name: 'Submit answer' });
    expect(submit).toHaveClass('min-h-11', 'md:min-h-0');
    await userEvent.click(submit);
    expect(onAnswer).toHaveBeenCalledWith({ answers: [{ question_id: 'q1', text: 'Use a local model' }] });
  });

  it('counts down from the server deadline and applies the recommendation', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-20T12:00:00Z'));
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    render(
      <QuestionCard
        question={question({
          auto_submit_seconds: 60,
          auto_submit_at: '2026-07-20T12:01:00Z',
        })}
        onAnswer={onAnswer}
        onCancel={vi.fn()}
        onStopTimer={vi.fn()}
      />,
    );

    expect(screen.getByText(/Using recommendation in 60s/)).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(60_000); });
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(onAnswer).toHaveBeenCalledWith({ answers: [{ question_id: 'q1', option_ids: ['deep'] }] });
  });

  it('can stop the automatic choice without cancelling the question', async () => {
    const onStopTimer = vi.fn().mockResolvedValue(undefined);
    render(
      <QuestionCard
        question={question({
          auto_submit_seconds: 60,
          auto_submit_at: new Date(Date.now() + 60_000).toISOString(),
        })}
        onAnswer={vi.fn()}
        onCancel={vi.fn()}
        onStopTimer={onStopTimer}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Stop countdown' }));
    expect(onStopTimer).toHaveBeenCalledTimes(1);
  });
  it('walks through several questions with multiple choice, something else and skip', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    render(
      <QuestionCard
        question={question({
          questions: [
            { id: 'db', prompt: 'Which database?', short: 'Database', options: [
              { id: 'sqlite', label: 'SQLite' }, { id: 'pg', label: 'Postgres' },
            ], recommended_option_id: 'sqlite' },
            { id: 'auth', prompt: 'Which sign-ins?', multiple: true, options: [
              { id: 'google', label: 'Google' }, { id: 'github', label: 'GitHub' },
            ], recommended_option_ids: ['google'] },
            { id: 'name', prompt: 'What should it be called?', options: [], allow_free_text: true },
            { id: 'icon', prompt: 'Which icon?', options: [{ id: 'a', label: 'A' }] },
          ],
        })}
        onAnswer={onAnswer}
        onCancel={vi.fn()}
        onStopTimer={vi.fn()}
      />,
    );

    expect(screen.getByText('Question 1 of 4')).toBeInTheDocument();
    // "Something else" replaces the pick with typed words.
    await userEvent.click(screen.getByRole('button', { name: 'Something else…' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Your answer' }), 'MySQL');
    expect(screen.getByRole('radio', { name: /SQLite/ })).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    expect(screen.getByText('Which sign-ins?')).toBeInTheDocument();
    expect(screen.getByText('Pick any · 1 selected')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: /GitHub/ }));
    expect(screen.getByText('Pick any · 2 selected')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    await userEvent.type(screen.getByRole('textbox', { name: 'Your answer' }), 'Ledgerly{Enter}');

    expect(screen.getByText('Which icon?')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('textbox', { name: 'Your answer' })).toHaveValue('Ledgerly');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await userEvent.click(screen.getByRole('button', { name: 'Skip' }));

    expect(onAnswer).toHaveBeenCalledWith({ answers: [
      { question_id: 'db', text: 'MySQL' },
      { question_id: 'auth', option_ids: ['google', 'github'] },
      { question_id: 'name', text: 'Ledgerly' },
      { question_id: 'icon', skipped: true },
    ] });
  });

  it('picks with number keys', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    render(<QuestionCard question={question()} onAnswer={onAnswer} onCancel={vi.fn()} onStopTimer={vi.fn()} />);
    screen.getByRole('radio', { name: /Quick pass/i }).focus();
    await userEvent.keyboard('2');
    expect(screen.getByRole('radio', { name: /Deep research/i })).toHaveAttribute('aria-checked', 'true');
    await userEvent.keyboard('1{Enter}');
    expect(onAnswer).toHaveBeenCalledWith({ answers: [{ question_id: 'q1', option_ids: ['quick'] }] });
  });

  it('cancels the question', async () => {
    const onCancel = vi.fn().mockResolvedValue(undefined);
    render(<QuestionCard question={question()} onAnswer={vi.fn()} onCancel={onCancel} onStopTimer={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Cancel question' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
  it('leaves Enter on other buttons alone', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    const onCancel = vi.fn().mockResolvedValue(undefined);
    render(<QuestionCard question={question()} onAnswer={onAnswer} onCancel={onCancel} onStopTimer={vi.fn()} />);
    screen.getByRole('button', { name: 'Cancel question' }).focus();
    await userEvent.keyboard('{Enter}');
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onAnswer).not.toHaveBeenCalled();
  });
});
