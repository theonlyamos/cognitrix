import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChatTokenStats, chatCost, tokenSummary, type ChatTokenTotals, type ChatTokenUsage } from './ChatTokenStats';

const totals = (patch: Partial<ChatTokenTotals> = {}): ChatTokenTotals => ({
  calls: 0, prompt_tokens: 0, completion_tokens: 0, cached_tokens: 0, cache_write_tokens: 0, cost: 0, unpriced_tokens: 0, ...patch,
});

const usage = (patch: Partial<ChatTokenUsage> = {}): ChatTokenUsage => ({
  tracked: true,
  prices_available: true,
  total: totals({ calls: 12, prompt_tokens: 184_300, completion_tokens: 6_420, cached_tokens: 151_000, cost: 0.4213 }),
  last_turn: totals({ calls: 3, prompt_tokens: 41_200, completion_tokens: 1_310, cached_tokens: 38_000, cost: 0.0617 }),
  by_model: [],
  context: { tokens: 42_510, window: 200_000, provider: 'anthropic', model: 'claude-opus-5-5' },
  ...patch,
});

describe('ChatTokenStats', () => {
  it('shows the chat and the last reply side by side, with the context meter', () => {
    render(<ChatTokenStats usage={usage()} />);
    expect(screen.getByText('43k of 200k · 21%')).toBeInTheDocument();
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '21');
    const input = screen.getByRole('row', { name: /Input/ });
    expect(input).toHaveTextContent('184k');
    expect(input).toHaveTextContent('41k');
    expect(screen.getByRole('row', { name: /Cached/ })).toHaveTextContent('151k');
    expect(screen.getByRole('row', { name: /Cost/ })).toHaveTextContent('$0.42');
    expect(screen.getByRole('row', { name: /Cost/ })).toHaveTextContent('$0.06');
    expect(screen.getByText(/12 model calls/)).toBeInTheDocument();
  });

  it('shows a dash for cost when the model has no known price', () => {
    const t = totals({ calls: 1, prompt_tokens: 100, completion_tokens: 10, unpriced_tokens: 110 });
    render(<ChatTokenStats usage={usage({ prices_available: false, total: t, last_turn: t, context: null })} />);
    expect(screen.getByRole('row', { name: /Cost/ })).toHaveTextContent('——');
    expect(screen.queryByRole('row', { name: /Cached/ })).toBeNull();
    expect(screen.getByText(/no known price/)).toBeInTheDocument();
  });

  it('says when counting starts for an older chat, keeping its context', () => {
    render(<ChatTokenStats usage={usage({ tracked: false, total: totals(), last_turn: null })} />);
    expect(screen.getByText(/start with this chat’s next reply/)).toBeInTheDocument();
    expect(screen.getByRole('meter')).toBeInTheDocument();
  });

  it('formats small costs and the header figure', () => {
    expect(chatCost(0)).toBe('$0.00');
    expect(chatCost(0.00423)).toBe('$0.0042');
    expect(chatCost(1.234)).toBe('$1.23');
    expect(tokenSummary(usage())).toBe('191k');
    expect(tokenSummary(null)).toBe('');
  });
});
