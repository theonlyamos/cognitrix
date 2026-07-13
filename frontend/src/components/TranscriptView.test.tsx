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

  it('announces completed and failed tool statuses while retaining their visible icons', () => {
    const { container } = render(<TranscriptView entries={[{
      kind: 'tool_calls',
      content: '',
      tools: [
        { name: 'read_file', args: '{}', status: 'done', result: 'contents' },
        { name: 'write_file', args: '{}', status: 'error', result: 'permission denied' },
      ],
    }]} />);

    expect(screen.getByText('done')).toHaveClass('sr-only');
    expect(screen.getByText('error')).toHaveClass('sr-only');
    expect(container.querySelectorAll('[aria-hidden="true"]')).toHaveLength(2);
  });

  it('keeps a tool with unknown status neutral', () => {
    const { container } = render(<TranscriptView entries={[{
      kind: 'tool_calls',
      content: '',
      tools: [{ name: 'read_file', args: '{}', result: 'contents' }],
    }]} />);

    const summary = container.querySelector('summary');
    expect(summary).toHaveTextContent('·read file');
    expect(summary).not.toHaveTextContent('✓');
    expect(summary).not.toHaveTextContent('✕');
    expect(screen.queryByText('done')).not.toBeInTheDocument();
    expect(screen.queryByText('error')).not.toBeInTheDocument();
    expect(screen.queryByText('running…')).not.toBeInTheDocument();
  });

  it('shows the error status and result when a running tool fails', async () => {
    const { rerender } = render(<TranscriptView entries={[{
      kind: 'tool_calls',
      content: '',
      tools: [{ name: 'write_file', args: '{}', status: 'running' }],
    }]} />);

    expect(screen.getByText('running…')).toBeInTheDocument();

    rerender(<TranscriptView entries={[{
      kind: 'tool_calls',
      content: '',
      tools: [{
        name: 'write_file',
        args: '{}',
        status: 'error',
        result: 'permission denied',
      }],
    }]} />);

    expect(screen.getByText('error')).toHaveClass('sr-only');
    expect(await screen.findByText('permission denied')).toBeInTheDocument();
  });

  it('does not materialize raw HTML from completed Markdown as DOM', async () => {
    const { container } = render(
      <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
        <TranscriptView entries={[{
          kind: 'assistant',
          content: '<img src="x" alt="unsafe image"><script>window.pwned = true</script>',
        }]} />
      </MemoryRouter>,
    );

    await screen.findByText(/<img src="x"/);
    expect(container.querySelector('img')).not.toBeInTheDocument();
    expect(container.querySelector('script')).not.toBeInTheDocument();
  });
});
