import { afterEach, describe, expect, it, vi } from 'vitest';
import { answerApproval, approvalPreview, approvalTitle } from './approvals';
import { isLookingAtChat, setViewingChat } from './chatPresence';
import type { InboxApproval } from '@/context/NotificationsContext';

const apiPost = vi.hoisted(() => vi.fn().mockResolvedValue({ data: {} }));
vi.mock('@/lib/api', () => ({ api: { post: apiPost } }));

const base: InboxApproval = {
  kind: 'approval',
  id: 'web-approval-abc',
  run_id: '',
  task_id: null,
  step_index: null,
  tool_name: 'Delete_File',
  params: { path: 'notes.txt' },
  risk_level: 'high',
  details: '',
  status: 'pending',
  created_at: '',
  task_title: '',
};

describe('approvals', () => {
  afterEach(() => {
    apiPost.mockClear();
    setViewingChat(null);
  });

  it('answers a chat approval through the chat endpoint', async () => {
    await answerApproval({ ...base, source: 'chat', session_id: 's1' }, true);
    expect(apiPost).toHaveBeenCalledWith('/agents/approval', { request_id: 'web-approval-abc', approved: true });
  });

  it('answers a task approval through its run', async () => {
    await answerApproval({ ...base, id: 'ap1', task_id: 't1', run_id: 'r1' }, false);
    expect(apiPost).toHaveBeenCalledWith('/tasks/t1/runs/r1/approvals/ap1', { approved: false });
  });

  it('says who asks and shows a command as itself', () => {
    expect(approvalTitle({ ...base, source: 'chat', agent_name: 'Helper' })).toBe('Helper wants to run Delete File');
    expect(approvalTitle({ ...base, task_title: 'Nightly' })).toBe('Nightly wants to run Delete File');
    expect(approvalPreview({ params: { command: 'rm  -rf\nbuild' } })).toBe('rm -rf build');
    expect(approvalPreview({ params: { a: 'x'.repeat(500) } }, 20)).toHaveLength(20);
  });

  it('only counts the open chat while the window is in front', () => {
    const focus = vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    setViewingChat('s1');
    expect(isLookingAtChat('s1')).toBe(true);
    expect(isLookingAtChat('s2')).toBe(false);
    focus.mockReturnValue(false);
    expect(isLookingAtChat('s1')).toBe(false);
    focus.mockRestore();
  });
});
