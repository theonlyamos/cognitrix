import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TaskApprovals } from './TaskApprovals';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn().mockResolvedValue({ data: {} }));
vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost },
  errorMessage: (_err: unknown, fallback: string) => fallback,
}));

const pending = {
  id: 'ap-1',
  run_id: 'run-1',
  step_index: 0,
  tool_name: 'Write',
  params: { file_path: 'report.md' },
  risk_level: 'medium',
  details: 'writes a file',
  status: 'pending',
  can_decide: true,
};

describe('TaskApprovals', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockClear();
  });

  it('shows a parked tool call and sends the approval', async () => {
    apiGet.mockResolvedValue({ data: [pending] });
    render(<TaskApprovals taskId="task-1" runId="run-1" live={false} />);

    expect(await screen.findByText(/Waiting for approval: Write/)).toBeInTheDocument();
    expect(screen.getByText(/report\.md/)).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith('/tasks/task-1/runs/run-1/approvals', { params: { status: 'pending' } });

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    expect(apiPost).toHaveBeenCalledWith('/tasks/task-1/runs/run-1/approvals/ap-1', { approved: true });
    await waitFor(() => expect(screen.queryByText(/Waiting for approval/)).not.toBeInTheDocument());
  });

  it('hides the buttons from people who did not start the run', async () => {
    apiGet.mockResolvedValue({ data: [{ ...pending, can_decide: false }] });
    render(<TaskApprovals taskId="task-1" runId="run-1" live={false} />);

    expect(await screen.findByText(/Only the person who started this run/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
  });

  it('renders nothing when no call is waiting', async () => {
    apiGet.mockResolvedValue({ data: [] });
    const { container } = render(<TaskApprovals taskId="task-1" runId="run-1" live={false} />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
