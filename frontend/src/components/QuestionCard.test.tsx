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

  it('uses the inline gate theme and submits an option', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    render(
      <QuestionCard
        question={question()}
        onAnswer={onAnswer}
        onCancel={vi.fn()}
        onStopTimer={vi.fn()}
      />,
    );

    expect(screen.getByRole('group', { name: 'How should I continue?' })).toHaveClass(
      'border-l-2',
      'border-accent',
    );
    expect(screen.getByText('recommended')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Deep research/i }));
    expect(onAnswer).toHaveBeenCalledWith({ option_id: 'deep' });
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
    expect(onAnswer).toHaveBeenCalledWith({ text: 'Use a local model' });
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
    expect(onAnswer).toHaveBeenCalledWith({ option_id: 'deep' });
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
});
