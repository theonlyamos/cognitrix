import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { UsageSettings, formatCost, formatTokens } from './Usage';

const get = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get },
}));

const totals = { calls: 3, prompt_tokens: 1_500_000, completion_tokens: 20_000, cached_tokens: 750_000, cache_write_tokens: 0, cost: 4.2, unpriced_tokens: 0, api_cost: 9.5, api_unpriced_tokens: 0, cache_saved: 2.25 };
const part = { prompt_tokens: 1_000_000, completion_tokens: 15_000, cached_tokens: 700_000, cost: 0, api_cost: 5.3 };
const summary = (over = {}) => ({
  days: 30,
  prices_available: true,
  total: totals,
  by_day: [
    { ...totals, day: '2026-11-08', calls: 0, cost: 0, api_cost: 0, prompt_tokens: 0, completion_tokens: 0, cached_tokens: 0, models: {}, agents: {} },
    { ...totals, day: '2026-11-09',
      models: { 'claude-code/opus': part, 'anthropic/claude-opus-5-5': { ...part, prompt_tokens: 500_000, completion_tokens: 5_000, cached_tokens: 50_000, cost: 4.2, api_cost: 4.2 } },
      agents: { a1: { ...part, prompt_tokens: 1_500_000, completion_tokens: 20_000, cached_tokens: 750_000, cost: 4.2, api_cost: 9.5 } } },
  ],
  by_model: [
    { ...totals, provider: 'claude-code', model: 'opus', prompt_tokens: 1_000_000, completion_tokens: 15_000, cached_tokens: 700_000, cost: 0, unpriced_tokens: 1_015_000, api_cost: 5.3 },
    { ...totals, provider: 'anthropic', model: 'claude-opus-5-5', prompt_tokens: 500_000, completion_tokens: 5_000, cached_tokens: 50_000, cost: 4.2, api_cost: 4.2 },
  ],
  by_agent: [{ ...totals, agent_id: 'a1', agent_name: 'Assistant' }],
  ...over,
});

beforeEach(() => { get.mockReset(); localStorage.clear(); });

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
  it('starts on tokens, with tiles, the day chart, panels and the output strip', async () => {
    get.mockResolvedValue({ data: summary() });
    render(<UsageSettings />);
    expect(await screen.findByText('Tokens per day')).toBeInTheDocument();
    expect(screen.getByText('50% read from cache')).toBeInTheDocument();
    expect(screen.getByText('20k out')).toBeInTheDocument();
    expect(screen.getAllByText('opus').length).toBeGreaterThan(0);
    expect(screen.getByText('Assistant')).toBeInTheDocument();
    expect(screen.getByText('2026-11-09: 1.5M, 20k out')).toBeInTheDocument();
    expect(screen.getByText(/750k of 1.5M input read from cache/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: '7 days' }));
    await waitFor(() => expect(get).toHaveBeenLastCalledWith('/usage', { params: { days: 7 } }));
  });

  it('switches to cost: paid next to what plan use is worth at API prices', async () => {
    get.mockResolvedValue({ data: summary() });
    render(<UsageSettings />);
    fireEvent.click(await screen.findByRole('radio', { name: 'Cost' }));
    expect(screen.getByText('Cost per day at API prices')).toBeInTheDocument();
    expect(screen.getByText('you paid $4.20, plans covered the rest')).toBeInTheDocument();
    expect(screen.getByText('$5.30 covered by plans')).toBeInTheDocument();
    expect(screen.getByText('$2.25')).toBeInTheDocument();
    // Remembered for next time.
    expect(localStorage.getItem('usage.metric')).toBe('cost');
  });

  it('splits the day chart by agent or by type', async () => {
    get.mockResolvedValue({ data: summary() });
    render(<UsageSettings />);
    fireEvent.click(await screen.findByRole('radio', { name: 'Type' }));
    expect(screen.getByText(/^New input/)).toBeInTheDocument();
    expect(screen.getByText(/^Output, below the line/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Agent' }));
    expect(screen.getAllByText(/^Assistant/).length).toBe(2);
  });

  it('shows tokens only when prices could not be loaded', async () => {
    get.mockResolvedValue({ data: summary({ prices_available: false }) });
    render(<UsageSettings />);
    expect(await screen.findByText('Tokens per day')).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'Cost' })).not.toBeInTheDocument();
    expect(screen.getByText(/could not be loaded/)).toBeInTheDocument();
  });

  it('says so when nothing has been used yet', async () => {
    const empty = { ...totals, calls: 0, prompt_tokens: 0, completion_tokens: 0, cached_tokens: 0, cost: 0 };
    get.mockResolvedValue({ data: summary({ total: empty, by_model: [], by_agent: [] }) });
    render(<UsageSettings />);
    expect(await screen.findByText(/Nothing yet for this period/)).toBeInTheDocument();
  });
});
