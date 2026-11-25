import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ChatMessageRow } from '@/components/ChatMessageRow';

describe('thinking in a reply', () => {
  it('shows the moving bars while the model is still thinking, and opens to the reasoning', async () => {
    const { container } = render(
      <ChatMessageRow
        message={{ id: 'a1', role: 'assistant', content: '<think>Check the logs first' }}
        isLast
        streaming
      />,
    );
    expect(container.querySelector('.think-bars')).toBeTruthy();
    const toggle = screen.getByRole('button', { name: /Thinking…/ });
    expect(screen.queryByText('Check the logs first')).toBeNull();
    await userEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('Check the logs first')).toBeTruthy();
  });

  it('keeps the reasoning folded above a finished reply', async () => {
    const { container } = render(
      <ChatMessageRow
        message={{ id: 'a2', role: 'assistant', content: '<think>Plan A then B</think>\nAll done.' }}
        isLast
        streaming={false}
      />,
    );
    expect(container.querySelector('.think-bars')).toBeNull();
    expect(screen.getByText('All done.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: /Thinking/ }));
    expect(screen.getByText('Plan A then B')).toBeTruthy();
  });
});
