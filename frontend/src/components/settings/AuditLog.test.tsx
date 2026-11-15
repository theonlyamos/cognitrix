import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AuditLog } from './AuditLog';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get },
}));

const now = Math.floor(Date.now() / 1000);
const event = (over = {}) => ({
  id: 'e1', at: now, kind: 'tool', action: 'Bash', user_id: 'u2', actor: 'bob@example.com', agent_id: 'a1',
  agent_name: 'Researcher', target: '', detail: 'command=ls -la', outcome: 'ok', risk: 'low', ...over,
});

beforeEach(() => {
  get.mockReset();
  get.mockResolvedValue({
    data: {
      admin: true, retention_days: 90, next_before: null,
      people: [{ id: 'u1', name: 'Ada', email: 'ada@example.com' }, { id: 'u2', name: 'Bob', email: 'bob@example.com' }],
      events: [
        event(),
        event({ id: 'e2', action: 'Delete File', outcome: 'denied', risk: 'high', detail: 'path=/tmp/x | refused by a person' }),
        event({ id: 'e3', kind: 'people', action: 'Removed', actor: 'ada@example.com', target: 'carol@example.com', agent_name: '', detail: 'deleted their work' }),
      ],
    },
  });
});

describe('AuditLog', () => {
  it('says who and which agent did what, and filters by person', async () => {
    render(<AuditLog />);
    expect(await screen.findByText('TODAY')).toBeInTheDocument();
    expect(screen.getAllByText('Researcher')).toHaveLength(2);
    expect(screen.getByText('refused')).toBeInTheDocument();
    expect(screen.getByText('high risk')).toBeInTheDocument();
    expect(screen.getByText('carol@example.com')).toBeInTheDocument();
    expect(screen.getByText(/You see everyone on this server/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Person'), { target: { value: 'u2' } });
    await waitFor(() => expect(get).toHaveBeenLastCalledWith('/audit', expect.objectContaining({ params: expect.objectContaining({ person: 'u2' }) })));
    fireEvent.change(screen.getByLabelText('Result'), { target: { value: 'denied' } });
    await waitFor(() => expect(get).toHaveBeenLastCalledWith('/audit', expect.objectContaining({ params: expect.objectContaining({ outcome: 'denied' }) })));
  });

  it('tells a member they only see their own entries', async () => {
    get.mockResolvedValue({ data: { admin: false, retention_days: 90, next_before: null, events: [] } });
    render(<AuditLog />);
    expect(await screen.findByText(/These are your own entries/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Person')).not.toBeInTheDocument();
  });
});
