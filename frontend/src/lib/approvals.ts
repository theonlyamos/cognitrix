import { api } from '@/lib/api';
import type { InboxApproval } from '@/context/NotificationsContext';

/** Answer an approval from the Inbox or a notification, whether a chat or a
 *  task run is waiting on it. */
export async function answerApproval(item: InboxApproval, approved: boolean): Promise<void> {
  if (item.source === 'chat') {
    await api.post('/agents/approval', { request_id: item.id, approved });
  } else if (item.task_id) {
    await api.post(`/tasks/${item.task_id}/runs/${item.run_id}/approvals/${item.id}`, { approved });
  }
}

/** One line about what the tool call does: a shell command as itself. */
export function approvalPreview(item: Pick<InboxApproval, 'params'>, limit = 200): string {
  const params = item.params ?? {};
  const text = typeof params.command === 'string' ? params.command : JSON.stringify(params);
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > limit ? `${flat.slice(0, limit - 1)}…` : flat;
}

export function approvalTitle(item: InboxApproval): string {
  const tool = item.tool_name.replace(/_/g, ' ');
  if (item.source === 'chat') return `${item.agent_name || 'An agent'} wants to run ${tool}`;
  return `${item.task_title || 'A task'} wants to run ${tool}`;
}
