import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { WaitingMessages } from '@/components/WaitingMessages';
import { parseChatEntries, toChatMessages } from '@/lib/transcript';

describe('WaitingMessages', () => {
  it('shows each waiting message with send now, edit and remove', async () => {
    const onSendNow = vi.fn();
    const onEdit = vi.fn();
    const onRemove = vi.fn();
    const first = { id: 'a', content: 'skip the e2e tests' };
    render(
      <WaitingMessages
        messages={[first, { id: 'b', content: 'then post it', source: 'telegram' }]}
        onSendNow={onSendNow}
        onEdit={onEdit}
        onRemove={onRemove}
      />,
    );
    expect(screen.getByText('skip the e2e tests')).toBeTruthy();
    expect(screen.getByText(/from telegram/)).toBeTruthy();
    await userEvent.click(screen.getAllByRole('button', { name: 'send now' })[0]);
    await userEvent.click(screen.getAllByRole('button', { name: 'edit' })[0]);
    await userEvent.click(screen.getAllByRole('button', { name: 'remove' })[0]);
    expect(onSendNow).toHaveBeenCalledTimes(1);
    expect(onEdit).toHaveBeenCalledWith(first);
    expect(onRemove).toHaveBeenCalledWith(first);
  });

  it('renders nothing when no message is waiting', () => {
    const { container } = render(
      <WaitingMessages messages={[]} onSendNow={vi.fn()} onEdit={vi.fn()} onRemove={vi.fn()} />,
    );
    expect(container.innerHTML).toBe('');
  });
});

describe('saved mid-task messages', () => {
  it('load as your messages, marked as sent while the agent worked', () => {
    const messages = toChatMessages(parseChatEntries([
      { role: 'User', type: 'text', content: 'cut the release' },
      { role: 'User', type: 'steer', content: 'skip e2e' },
      { role: 'assistant', type: 'text', content: 'done' },
    ]));
    expect(messages.map((m) => [m.role, m.content, !!m.steered])).toEqual([
      ['user', 'cut the release', false],
      ['user', 'skip e2e', true],
      ['assistant', 'done', false],
    ]);
  });
});
