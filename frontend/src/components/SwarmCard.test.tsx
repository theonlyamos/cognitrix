import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SwarmCard } from './SwarmCard';
import type { Swarm, SwarmWorker } from '@/lib/swarms';

const worker = (name: string, patch: Partial<SwarmWorker> = {}): SwarmWorker => ({
  id: name, name, role: 'Researcher', agent_id: 'a2', agent_name: 'Researcher', model: '', brief: `find ${name}`,
  depends_on: [], may_edit: false, status: 'running', stalled: false, chat_id: `chat-${name}`, summary: '', error: '',
  tokens: 1200, review: '', changed: 0, branch: '', merged: false, added_by: '', last_step: 'Web Search', ...patch,
});

const swarm = (patch: Partial<Swarm> = {}): Swarm => ({
  id: 's1', session_id: 'c1', agent_name: 'Assistant', title: 'Pricing research', mode: 'fanout', status: 'running',
  stop_reason: '', judge: {},
  ledger: { total: 3, done: 1, failed: 0, stopped: 0, running: 1, queued: 1, tokens: 5200, token_budget: 2_000_000 },
  started_at: '', finished_at: '',
  workers: [
    worker('acme', { status: 'done', summary: 'Acme charges $10 a seat.', last_step: '' }),
    worker('globex'),
    worker('initech', { status: 'queued', chat_id: '', depends_on: ['acme'], last_step: '' }),
  ],
  ...patch,
});

describe('SwarmCard', () => {
  it('shows progress and what each worker is doing, and stops one worker or all', async () => {
    const user = userEvent.setup();
    const onStop = vi.fn();
    const onStopWorker = vi.fn();
    const onOpenChat = vi.fn();
    render(<SwarmCard swarm={swarm()} onStop={onStop} onStopWorker={onStopWorker} onOpenChat={onOpenChat} />);

    const card = screen.getByRole('region', { name: 'Swarm Pricing research' });
    expect(within(card).getByText(/1 of 3 done/)).toBeInTheDocument();
    expect(within(card).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1');
    expect(within(card).getByText('Acme charges $10 a seat.')).toBeInTheDocument();
    expect(within(card).getByText('Using Web Search')).toBeInTheDocument();
    expect(within(card).getByText('Waits for acme')).toBeInTheDocument();

    await user.click(within(card).getAllByRole('button', { name: 'Open its chat' })[1]);
    expect(onOpenChat).toHaveBeenCalledWith('chat-globex');
    await user.click(within(card).getAllByRole('button', { name: 'Stop' })[0]);
    expect(onStopWorker).toHaveBeenCalledWith('s1', 'globex');
    await user.click(within(card).getByRole('button', { name: 'Stop all' }));
    expect(onStop).toHaveBeenCalledWith('s1');
  });

  it('is folded once finished, marks the picked attempt and offers a merge', async () => {
    const user = userEvent.setup();
    const onMerge = vi.fn().mockResolvedValue('Merged 2 changed file(s).');
    render(
      <SwarmCard
        swarm={swarm({
          status: 'done', mode: 'best_of', judge: { winner: 'attempt-2', verdict: 'Attempt 2 covers every case.' },
          ledger: { total: 2, done: 2, failed: 0, stopped: 0, running: 0, queued: 0, tokens: 900, token_budget: 0 },
          workers: [
            worker('attempt-1', { status: 'done', summary: 'First try.' }),
            worker('attempt-2', { status: 'done', summary: 'Second try.', branch: 'cognitrix-swarm/s1/attempt-2', changed: 2 }),
          ],
        })}
        onStop={vi.fn()}
        onStopWorker={vi.fn()}
        onMerge={onMerge}
      />,
    );
    expect(screen.queryByRole('button', { name: 'Stop all' })).not.toBeInTheDocument();
    expect(screen.queryByText('Second try.')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Pricing research/ }));
    expect(screen.getByText('PICKED')).toBeInTheDocument();
    expect(screen.getByText('Attempt 2 covers every case.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Merge 2 changed files' }));
    expect(onMerge).toHaveBeenCalledWith('s1', 'attempt-2');
    expect(await screen.findByText('Merged 2 changed file(s).')).toBeInTheDocument();
  });
});
