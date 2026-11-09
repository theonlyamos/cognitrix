import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { UsageSettings, formatCost, formatTokens } from './Usage';

const get = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get },
}));

const totals = { calls: 3, prompt_tokens: 1_500_000, completion_tokens: 20_000, cached_tokens: 750_000, cache_write_tokens: 0, cost: 4.2, unpriced_tokens: 0 };
const summary = (over = {}) => ({
  days: 30,
  prices_available: true,
  total: totals,
  by_day: [{ ...totals, day: '2026-11-08', calls: 0, cost: 0, prompt_tokens: 0, completion_tokens: 0 }, { ...totals, day: '2026-11-09' }],
  by_model: [{ ...totals, provider: 'anthropic', model: 'claude-opus-5-5' }],
  by_agent: [{ ...totals, agent_id: 'a1', agent_name: 'Assistant' }],
  ...over,
});

beforeEach(() => { get.mockReset(); });

describe('usage formatting', () => {
  it('shortens tokens and keeps tiny costs honest', () => {
    expect(formatTokens(950)).toBe('950');
    expect(formatTokens(1_500)).toBe('1.5k');
    expect(formatTokens(2_300_000)).toBe('2.3M');
    expect(formatCost(0)).toBe('$0.00');
    expect(formatCost(0.004)).toBe('under $0.01');
    expect(formatCost(4.2)).toBe('$4.20');
  });
});

describe('UsageSettings', () => {
  it('shows cost, tokens and the breakdowns, and reloads for another period', async () => {
    get.mockResolvedValue({ data: summary() });
    render(<UsageSettings />);
    expect(await screen.findByText('ESTIMATED COST')).toBeInTheDocument();
    expect(screen.getAllByText('$4.20').length).toBeGreaterThan(0);
    expect(screen.getByText('50% read from cache')).toBeInTheDocument();
    expect(screen.getByText('claude-opus-5-5')).toBeInTheDocument();
    expect(screen.getByText('Assistant')).toBeInTheDocument();
    expect(screen.getByText('2026-11-09: $4.20')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: '7 days' }));
    await waitFor(() => expect(get).toHaveBeenLastCalledWith('/usage', { params: { days: 7 } }));
  });

  it('shows tokens only when prices could not be loaded', async () => {
    get.mockResolvedValue({ data: summary({ prices_available: false }) });
    render(<UsageSettings />);
    expect(await screen.findByText('TOKENS PER DAY')).toBeInTheDocument();
    expect(screen.queryByText('ESTIMATED COST')).not.toBeInTheDocument();
    expect(screen.getByText(/could not be loaded/)).toBeInTheDocument();
  });

  it('says so when nothing has been used yet', async () => {
    const empty = { ...totals, calls: 0, prompt_tokens: 0, completion_tokens: 0, cached_tokens: 0, cost: 0 };
    get.mockResolvedValue({ data: summary({ total: empty, by_model: [], by_agent: [] }) });
    render(<UsageSettings />);
    expect(await screen.findByText(/Nothing yet for this period/)).toBeInTheDocument();
  });
});
