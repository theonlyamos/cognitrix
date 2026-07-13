import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { TranscriptView } from '@/components/TranscriptView';

describe('TranscriptView live and Markdown output', () => {
  it('renders completed assistant output as Markdown', async () => {
    render(
      <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
        <TranscriptView entries={[{
          kind: 'assistant',
          name: 'Writer',
          content: '# Result\n\n- one\n- two\n\n[task](/tasks/1)\n\n```ts\nconst value = 1;\n```',
        }]} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'Result' }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('link', { name: 'task' })).toHaveAttribute(
      'href',
      '/tasks/1',
    );
    expect(screen.getByRole('button', { name: 'copy' })).toBeInTheDocument();
  });

  it('keeps active output plain until completion', async () => {
    render(<TranscriptView entries={[{
      kind: 'assistant',
      content: '# still streaming',
      live: true,
    }]} />);

    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    expect(screen.getByText('# still streaming')).toBeInTheDocument();
  });

  it('shows a tool while running and its result on completion', async () => {
    const { rerender } = render(<TranscriptView entries={[{
      kind: 'tool_calls',
      content: '',
      tools: [{
        id: 'call-1',
        name: 'read_file',
        args: '{"path":"README.md"}',
        status: 'running',
      }],
    }]} />);
    expect(screen.getByText('running…')).toBeInTheDocument();

    rerender(<TranscriptView entries={[{
      kind: 'tool_calls',
      content: '',
      tools: [{
        id: 'call-1',
        name: 'read_file',
        args: '{"path":"README.md"}',
        status: 'done',
        result: 'contents',
      }],
    }]} />);
    expect(await screen.findByText('contents')).toBeInTheDocument();
  });
});
