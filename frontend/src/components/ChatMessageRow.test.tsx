import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render, screen, waitFor } from '@testing-library/react';
import type { ComponentType } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { ChatMessage } from '@/context/SessionContext';

const markdownRender = vi.fn();

vi.mock('@/components/MarkdownMessage', () => ({
  default: ({ content }: { content: string }) => {
    markdownRender();
    return <div>{content}</div>;
  },
}));

describe('ChatMessageRow', () => {
  it('does not rerender completed markdown when parent state changes', async () => {
    const rowPath = resolve(process.cwd(), 'src/components/ChatMessageRow.tsx');
    expect(existsSync(rowPath), 'ChatMessageRow component should exist').toBe(true);

    const modulePath = '@/components/ChatMessageRow';
    const { ChatMessageRow } = await import(modulePath) as {
      ChatMessageRow: ComponentType<{
        message: ChatMessage;
        isLast: boolean;
        streaming: boolean;
      }>;
    };
    const message: ChatMessage = {
      id: 'assistant-1',
      role: 'assistant',
      content: '**Stable** response',
      timestamp: '2026-07-10T08:30:00.000Z',
    };

    function Parent({ unrelated }: { unrelated: number }) {
      return (
        <>
          <span>parent state: {unrelated}</span>
          <ChatMessageRow message={message} isLast={false} streaming={false} />
        </>
      );
    }

    const { rerender } = render(<Parent unrelated={0} />);
    await waitFor(() => expect(markdownRender).toHaveBeenCalledTimes(1));

    rerender(<Parent unrelated={1} />);

    expect(screen.getByText('parent state: 1')).toBeInTheDocument();
    expect(markdownRender).toHaveBeenCalledTimes(1);
  });

  it('keeps markdown packages behind the Home route boundary', () => {
    const homeSource = readFileSync(
      resolve(process.cwd(), 'src/pages/Home.tsx'),
      'utf8',
    );

    expect(homeSource).not.toMatch(
      /from 'react-markdown'|from 'remark-gfm'|from 'rehype-highlight'/,
    );
  });
});
