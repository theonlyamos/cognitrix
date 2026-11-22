import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ReviewSection } from './MemoryReview';
import type { LearnedItem, ReviewStatus } from '@/lib/memories';

const lib = vi.hoisted(() => ({
  reviewStatus: vi.fn(),
  reviewNow: vi.fn(),
  removeLearned: vi.fn(),
  saveMemorySettings: vi.fn(),
}));

vi.mock('@/lib/memories', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/memories')>()),
  ...lib,
}));

const skill: LearnedItem = {
  id: 'l1', run_id: 'r1', kind: 'skill', action: 'created', target_id: 'p1', name: 'monthly-invoice-totals',
  description: 'Total last month\'s invoices by client.', content: '## Steps\n1. Open the billing export.',
  source_kind: 'chat', source_id: 's1', source_title: 'Invoice totals', source_link: '/home?chat=s1',
  created: '2026-11-22 03:05:00', removed: false,
};
const improved: LearnedItem = { ...skill, id: 'l2', action: 'updated', name: 'weekly-report' };
const fact: LearnedItem = {
  ...skill, id: 'l3', kind: 'memory', name: null, description: null, content: 'Rounds money to whole cedis.',
  source_kind: 'task', source_title: 'Pay run', source_link: '/tasks/t1',
};

const status = (over: Partial<ReviewStatus> = {}): ReviewStatus => ({
  enabled: true, running: false, hour: 3, learned: [skill, improved, fact],
  runs: [{ id: 'r1', trigger: 'night', status: 'done', started: '2026-11-22 03:00:00', finished: '2026-11-22 03:06:00',
    summary: 'Reviewed 2 finished chats and tasks: wrote 1 new skill.', stats: {} }],
  ...over,
});

function renderIt(onChanged = vi.fn()) {
  render(<MemoryRouter><ReviewSection enabled onChanged={onChanged} /></MemoryRouter>);
  return onChanged;
}

beforeEach(() => {
  Object.values(lib).forEach((fn) => fn.mockReset());
  lib.reviewStatus.mockResolvedValue(status());
});

describe('ReviewSection', () => {
  it('lists what the review kept with where it came from', async () => {
    renderIt();
    expect(await screen.findByText('monthly-invoice-totals')).toBeTruthy();
    expect(screen.getByText('NEW SKILL')).toBeTruthy();
    expect(screen.getByText('SKILL IMPROVED')).toBeTruthy();
    expect(screen.getByText('Rounds money to whole cedis.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Pay run' }).getAttribute('href')).toBe('/tasks/t1');
    expect(screen.getByText(/wrote 1 new skill/)).toBeTruthy();
  });

  it('shows a skill on View and removes or undoes items', async () => {
    lib.removeLearned.mockResolvedValue({ ...skill, removed: true });
    const onChanged = renderIt();
    await screen.findByText('monthly-invoice-totals');
    fireEvent.click(screen.getAllByRole('button', { name: 'View' })[0]);
    expect(screen.getByText(/Open the billing export/)).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Undo' })).toHaveLength(1);
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0]);
    await waitFor(() => expect(lib.removeLearned).toHaveBeenCalledWith('l1'));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('starts a review now and switches the nightly review off', async () => {
    lib.reviewNow.mockResolvedValue({ id: 'r2', status: 'running' });
    lib.saveMemorySettings.mockResolvedValue({});
    renderIt();
    fireEvent.click(await screen.findByRole('button', { name: 'Review now' }));
    await waitFor(() => expect(lib.reviewNow).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('checkbox', { name: 'Review chats each night' }));
    await waitFor(() => expect(lib.saveMemorySettings).toHaveBeenCalledWith({ review_enabled: false }));
  });
});
