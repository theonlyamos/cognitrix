import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AnsweredQuestions, answeredSummary } from './AnsweredQuestions';

const result = (value: unknown) => JSON.stringify(value);

describe('answeredSummary', () => {
  it('lists each question and what was chosen', () => {
    const summary = answeredSummary('Ask User', '{}', result({
      status: 'answered', answer_type: 'answers', auto_submitted: false, answers: [
        { question: 'Which database?', short: 'Database', choices: ['SQLite'], recommended: true, skipped: false },
        { question: 'Which sign-ins?', choices: ['Google', 'GitHub'], text: 'Apple', skipped: false },
        { question: 'Which icon?', choices: [], skipped: true },
      ],
    }))!;
    render(<AnsweredQuestions summary={summary} />);
    expect(screen.getByText('You answered 2 questions')).toBeInTheDocument();
    expect(screen.getByText('Which database?')).toBeInTheDocument();
    expect(screen.getByText('SQLite')).toBeInTheDocument();
    expect(screen.getByText('recommended')).toBeInTheDocument();
    expect(screen.getByText('Google, GitHub, Apple')).toBeInTheDocument();
    expect(screen.getByText('Skipped')).toBeInTheDocument();
  });

  it('reads older answers and replies typed in the chat box', () => {
    const summary = answeredSummary('ask_user', result({ questions: [{ prompt: 'A?', short: 'A' }, { prompt: 'B?' }] }),
      result({ status: 'answered', answer_type: 'text', text: 'yes and no' }))!;
    render(<AnsweredQuestions summary={summary} />);
    expect(screen.getByText('Replied in words')).toBeInTheDocument();
    expect(screen.getByText('A · B?')).toBeInTheDocument();
    expect(screen.getByText('yes and no')).toBeInTheDocument();
  });

  it('ignores other tools and unanswered calls', () => {
    expect(answeredSummary('Notify Me', '{}', result({ status: 'answered' }))).toBeNull();
    expect(answeredSummary('Ask User', '{}', 'Question cancelled by user')).toBeNull();
  });
});
